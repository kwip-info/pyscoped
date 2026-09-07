"""Tests for rule evaluation debugging — ConditionMatch, RuleExplanation, EvaluationExplanation."""

import pytest

from scoped.identity.principal import PrincipalStore
from scoped.rules.engine import RuleEngine, RuleStore
from scoped.rules.models import (
    BindingTargetType,
    ConditionMatch,
    EvaluationExplanation,
    RuleEffect,
    RuleExplanation,
    RuleType,
)


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    admin = store.create_principal(kind="user", display_name="Admin", principal_id="admin")
    user = store.create_principal(kind="user", display_name="User", principal_id="user1")
    return admin, user


@pytest.fixture
def store(sqlite_backend):
    return RuleStore(sqlite_backend)


@pytest.fixture
def engine(sqlite_backend):
    return RuleEngine(sqlite_backend)


class TestExplanationConditionMatches:

    def test_explanation_shows_matching_condition(self, store, engine, principals):
        """An ALLOW rule with an action condition that matches shows matched=True."""
        admin, _ = principals
        rule = store.create_rule(
            name="Allow read",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["read"]},
            created_by=admin.id,
        )
        store.bind_rule(
            rule.id,
            target_type=BindingTargetType.SCOPE,
            target_id="s1",
            bound_by=admin.id,
        )

        explanation = engine.evaluate_with_explanation(action="read", scope_id="s1")

        assert explanation.result.allowed
        assert len(explanation.explanations) == 1

        rule_exp = explanation.explanations[0]
        assert rule_exp.matched is True
        assert len(rule_exp.condition_matches) == 1

        cond = rule_exp.condition_matches[0]
        assert cond.condition_key == "action"
        assert cond.matched is True
        assert cond.expected == ["read"]
        assert cond.actual == "read"

    def test_explanation_shows_failed_condition(self, store, engine, principals):
        """A rule with action='update' evaluated with action='create' shows matched=False."""
        admin, _ = principals
        rule = store.create_rule(
            name="Allow update",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": "update"},
            created_by=admin.id,
        )
        store.bind_rule(
            rule.id,
            target_type=BindingTargetType.SCOPE,
            target_id="s1",
            bound_by=admin.id,
        )

        explanation = engine.evaluate_with_explanation(action="create", scope_id="s1")

        assert not explanation.result.allowed
        assert len(explanation.explanations) == 1

        rule_exp = explanation.explanations[0]
        assert rule_exp.matched is False
        assert len(rule_exp.condition_matches) == 1

        cond = rule_exp.condition_matches[0]
        assert cond.condition_key == "action"
        assert cond.matched is False
        assert cond.expected == "update"
        assert cond.actual == "create"


class TestExplanationSummaries:

    def test_deny_explanation_summary(self, store, engine, principals):
        """A matching DENY rule produces a summary starting with 'Denied by rule'."""
        admin, _ = principals
        rule = store.create_rule(
            name="block-invoices",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY,
            conditions={"action": "create", "object_type": "invoice"},
            priority=100,
            created_by=admin.id,
        )
        store.bind_rule(
            rule.id,
            target_type=BindingTargetType.SCOPE,
            target_id="s1",
            bound_by=admin.id,
        )

        explanation = engine.evaluate_with_explanation(
            action="create", scope_id="s1", object_type="invoice",
        )

        assert not explanation.result.allowed
        assert "Denied by rule 'block-invoices'" in explanation.summary
        assert "(priority 100)" in explanation.summary

    def test_allow_explanation_summary(self, store, engine, principals):
        """A matching ALLOW rule produces a summary starting with 'Allowed by'."""
        admin, _ = principals
        rule = store.create_rule(
            name="allow-reads",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["read"]},
            created_by=admin.id,
        )
        store.bind_rule(
            rule.id,
            target_type=BindingTargetType.SCOPE,
            target_id="s1",
            bound_by=admin.id,
        )

        explanation = engine.evaluate_with_explanation(action="read", scope_id="s1")

        assert explanation.result.allowed
        assert "Allowed by 1 rule(s)" in explanation.summary
        assert "no DENY rules matched" in explanation.summary

    def test_no_rules_explanation(self, engine, principals):
        """No rules bound produces a default-deny summary."""
        explanation = engine.evaluate_with_explanation(
            action="read", scope_id="s1",
        )

        assert not explanation.result.allowed
        assert "default-deny" in explanation.summary
        assert len(explanation.explanations) == 0


class TestExplanationComprehensiveness:

    def test_multiple_rules_all_explained(self, store, engine, principals):
        """Both matching and non-matching rules appear in explanations."""
        admin, _ = principals
        allow_rule = store.create_rule(
            name="allow-read",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["read"]},
            created_by=admin.id,
        )
        deny_rule = store.create_rule(
            name="deny-delete",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY,
            conditions={"action": ["delete"]},
            priority=10,
            created_by=admin.id,
        )
        store.bind_rule(
            allow_rule.id,
            target_type=BindingTargetType.SCOPE,
            target_id="s1",
            bound_by=admin.id,
        )
        store.bind_rule(
            deny_rule.id,
            target_type=BindingTargetType.SCOPE,
            target_id="s1",
            bound_by=admin.id,
        )

        explanation = engine.evaluate_with_explanation(action="read", scope_id="s1")

        # Both rules should be explained
        assert len(explanation.explanations) == 2

        names = {e.rule.name for e in explanation.explanations}
        assert "allow-read" in names
        assert "deny-delete" in names

        # The allow-read should match, deny-delete should not
        by_name = {e.rule.name: e for e in explanation.explanations}
        assert by_name["allow-read"].matched is True
        assert by_name["deny-delete"].matched is False

    def test_empty_conditions_match_everything(self, store, engine, principals):
        """A rule with empty conditions matches and has no condition_matches entries."""
        admin, _ = principals
        rule = store.create_rule(
            name="universal",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={},
            created_by=admin.id,
        )
        store.bind_rule(
            rule.id,
            target_type=BindingTargetType.SCOPE,
            target_id="s1",
            bound_by=admin.id,
        )

        explanation = engine.evaluate_with_explanation(action="read", scope_id="s1")

        assert explanation.result.allowed
        assert len(explanation.explanations) == 1

        rule_exp = explanation.explanations[0]
        assert rule_exp.matched is True
        assert len(rule_exp.condition_matches) == 0

    def test_explanation_includes_binding_info(self, store, engine, principals):
        """RuleExplanation populates binding_target_type and binding_target_id."""
        admin, _ = principals
        rule = store.create_rule(
            name="allow-scoped",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["read"]},
            created_by=admin.id,
        )
        store.bind_rule(
            rule.id,
            target_type=BindingTargetType.SCOPE,
            target_id="s1",
            bound_by=admin.id,
        )

        explanation = engine.evaluate_with_explanation(action="read", scope_id="s1")

        assert len(explanation.explanations) == 1
        rule_exp = explanation.explanations[0]
        assert rule_exp.binding_target_type == "scope"
        assert rule_exp.binding_target_id == "s1"
