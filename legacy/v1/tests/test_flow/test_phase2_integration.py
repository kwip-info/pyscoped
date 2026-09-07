"""Layer 9 Phase 2 integration tests.

Covers:
* ``env.promote(target_scope_id=...)`` auto-projects CREATED-origin objects.
* Module-level namespaces: ``scoped.pipelines``, ``scoped.flow``,
  ``scoped.promotions``.
* Rule-engine deny hooks on ``stage_transition`` and ``promotion``.
"""

from __future__ import annotations

import warnings

import pytest

import scoped
from scoped.exceptions import (
    EnvironmentStateError,
    PromotionDeniedError,
    StageTransitionDeniedError,
)
from scoped.flow.models import FlowPointType
from scoped.rules.models import BindingTargetType, RuleEffect, RuleType

warnings.filterwarnings("ignore")


@pytest.fixture
def client():
    return scoped.init()


# ----- env.promote auto-chains to PromotionManager ----------------------

class TestEnvPromoteAutoChain:

    def test_promote_with_target_scope_projects_created_objects(self, client):
        alice = client.principals.create("Alice")
        bob = client.principals.create("Bob")
        with client.as_principal(alice):
            env = client.environments.spawn("review")
            client.environments.activate(env)
            doc, _ = client.objects.create("invoice", data={"amount": 500})
            client.environments.add_object(env, doc)

            scope = client.scopes.create("Finance")
            client.scopes.add_member(scope, bob, role="editor")

            client.environments.complete(env)
            promoted = client.environments.promote(env, target_scope_id=scope)

        assert promoted.state.value == "promoted"
        # Bob (scope member) can now see the promoted object via VisibilityEngine
        assert client.services.visibility_engine.can_see(bob.id, doc.id)
        # A Promotion row also exists
        promos = client.services.promotions.list_promotions(source_env_id=env.id)
        assert any(p.object_id == doc.id for p in promos)

    def test_promote_without_target_scope_only_flips_state(self, client):
        alice = client.principals.create("Alice")
        with client.as_principal(alice):
            env = client.environments.spawn("review")
            client.environments.activate(env)
            doc, _ = client.objects.create("invoice", data={"amount": 1})
            client.environments.add_object(env, doc)
            client.environments.complete(env)
            promoted = client.environments.promote(env)

        assert promoted.state.value == "promoted"
        # No auto-promotion: promotions table empty for this env
        assert client.services.promotions.count_promotions(env.id) == 0

    def test_promote_skips_projected_origin_objects(self, client):
        """Objects pulled in via project_in aren't considered for auto-promotion."""
        alice = client.principals.create("Alice")
        with client.as_principal(alice):
            # Object that exists outside the env
            outside, _ = client.objects.create("memo", data={"text": "hi"})
            env = client.environments.spawn("review")
            client.environments.activate(env)
            # Pull it in as projected (not created inside the env)
            client.services.env_container.project_in(env.id, outside.id, actor_id=alice.id)

            # Also create a native object inside the env
            native, _ = client.objects.create("invoice", data={"amount": 10})
            client.environments.add_object(env, native)

            scope = client.scopes.create("Shared")
            client.environments.complete(env)
            client.environments.promote(env, target_scope_id=scope)

        promos = client.services.promotions.list_promotions(source_env_id=env.id)
        promoted_ids = {p.object_id for p in promos}
        assert native.id in promoted_ids
        assert outside.id not in promoted_ids

    def test_promote_propagates_promotion_failure(self, client):
        """A failed per-object promotion leaves the env in COMPLETED."""
        alice = client.principals.create("Alice")
        with client.as_principal(alice):
            env = client.environments.spawn("review")
            client.environments.activate(env)
            doc, _ = client.objects.create("invoice", data={"amount": 1})
            client.environments.add_object(env, doc)
            client.environments.complete(env)

            # target_scope doesn't exist → ScopeNotFoundError, state stays COMPLETED
            from scoped.exceptions import ScopeNotFoundError
            with pytest.raises(ScopeNotFoundError):
                client.environments.promote(env, target_scope_id="does-not-exist")

            fetched = client.services.environments.get(env.id)
            assert fetched.state.value == "completed"


# ----- Module-level namespaces -------------------------------------------

class TestModuleNamespaces:

    def test_scoped_pipelines_namespace_works(self, client):
        alice = client.principals.create("Alice")
        with client.as_principal(alice):
            pipe = scoped.pipelines.create("Code Review")
            draft = scoped.pipelines.add_stage(pipe, name="draft", ordinal=0)
            review = scoped.pipelines.add_stage(pipe, name="review", ordinal=1)

            doc, _ = client.objects.create("Doc", data={"title": "t"})
            scoped.pipelines.transition(doc, draft)
            scoped.pipelines.transition(doc, review, from_stage=draft)

            history = scoped.pipelines.history(doc)
            assert len(history) == 2
            current = scoped.pipelines.current_stage(doc)
            assert current.to_stage_id == review.id

    def test_scoped_flow_namespace_works(self, client):
        alice = client.principals.create("Alice")
        with client.as_principal(alice):
            env = client.environments.spawn("E")
            scope = client.scopes.create("S")
            ch = scoped.flow.create_channel(
                name="env->s",
                source_type=FlowPointType.ENVIRONMENT, source_id=env,
                target_type=FlowPointType.SCOPE, target_id=scope,
                allowed_types=["invoice"],
            )
            assert ch.name == "env->s"

            res = scoped.flow.can_flow(
                source_type=FlowPointType.ENVIRONMENT, source_id=env,
                target_type=FlowPointType.SCOPE, target_id=scope,
                object_type="invoice",
            )
            assert res.allowed

    def test_scoped_promotions_namespace_works(self, client):
        alice = client.principals.create("Alice")
        with client.as_principal(alice):
            env = client.environments.spawn("E")
            client.environments.activate(env)
            doc, _ = client.objects.create("invoice", data={"a": 1})
            client.environments.add_object(env, doc)
            scope = client.scopes.create("S")

            promo = scoped.promotions.promote(
                obj=doc, source_env=env, target_scope=scope,
            )
            assert promo.object_id == doc.id

            listed = scoped.promotions.list(source_env=env)
            assert len(listed) == 1
            assert scoped.promotions.count(env) == 1


# ----- Rule-engine hooks -------------------------------------------------

class TestRuleEngineHooks:

    def test_deny_rule_blocks_stage_transition(self, client):
        alice = client.principals.create("Alice")
        rules = client.services.rules
        rule = rules.create_rule(
            name="block-invoice-transitions",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY,
            conditions={"action": "stage_transition", "object_type": "invoice"},
            priority=100,
            created_by=alice.id,
        )
        rules.bind_rule(
            rule.id,
            target_type=BindingTargetType.OBJECT_TYPE,
            target_id="invoice",
            bound_by=alice.id,
        )

        with client.as_principal(alice):
            pipe = client.services.pipelines.create_pipeline(name="P", owner_id=alice.id)
            s = client.services.pipelines.add_stage(pipe.id, name="draft", ordinal=0)
            doc, _ = client.objects.create("invoice", data={"a": 1})

            with pytest.raises(StageTransitionDeniedError, match="denied by rule"):
                client.services.pipelines.transition(
                    doc.id, s.id, transitioned_by=alice.id,
                )

    def test_deny_rule_blocks_promotion(self, client):
        alice = client.principals.create("Alice")
        rules = client.services.rules
        rule = rules.create_rule(
            name="block-secret-promotions",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY,
            conditions={"action": "promotion", "object_type": "secret"},
            priority=100,
            created_by=alice.id,
        )
        rules.bind_rule(
            rule.id,
            target_type=BindingTargetType.OBJECT_TYPE,
            target_id="secret",
            bound_by=alice.id,
        )

        with client.as_principal(alice):
            env = client.environments.spawn("E")
            client.environments.activate(env)
            doc, _ = client.objects.create("secret", data={"value": "x"})
            client.environments.add_object(env, doc)
            scope = client.scopes.create("S")

            with pytest.raises(PromotionDeniedError, match="denied by rule"):
                client.services.promotions.promote(
                    object_id=doc.id,
                    source_env_id=env.id,
                    target_scope_id=scope.id,
                    promoted_by=alice.id,
                )

    def test_allow_rule_permits_promotion(self, client):
        """Sanity: when no rules match, promotion proceeds."""
        alice = client.principals.create("Alice")
        with client.as_principal(alice):
            env = client.environments.spawn("E")
            client.environments.activate(env)
            doc, _ = client.objects.create("doc", data={"x": 1})
            client.environments.add_object(env, doc)
            scope = client.scopes.create("S")

            promo = client.services.promotions.promote(
                object_id=doc.id,
                source_env_id=env.id,
                target_scope_id=scope.id,
                promoted_by=alice.id,
            )
            assert promo.object_id == doc.id
