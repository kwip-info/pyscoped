"""Tests for RuleStore and RuleEngine — CRUD + deny-overrides evaluation."""

import pytest

from scoped.identity.principal import PrincipalStore
from scoped.rules.engine import RuleEngine, RuleStore
from scoped.rules.models import (
    BindingTargetType,
    RuleEffect,
    RuleType,
)


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    admin = store.create_principal(kind="user", display_name="Admin", principal_id="admin")
    user = store.create_principal(kind="user", display_name="User", principal_id="user1")
    bot = store.create_principal(kind="bot", display_name="Bot", principal_id="bot1")
    return admin, user, bot


@pytest.fixture
def store(sqlite_backend):
    return RuleStore(sqlite_backend)


@pytest.fixture
def engine(sqlite_backend):
    return RuleEngine(sqlite_backend)


# -----------------------------------------------------------------------
# RuleStore CRUD
# -----------------------------------------------------------------------

class TestRuleStoreCRUD:

    def test_create_rule(self, store, principals):
        admin, _, _ = principals
        rule = store.create_rule(
            name="Allow reads",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["read"]},
            priority=5,
            created_by=admin.id,
        )
        assert rule.name == "Allow reads"
        assert rule.effect == RuleEffect.ALLOW
        assert rule.current_version == 1

    def test_get_rule(self, store, principals):
        admin, _, _ = principals
        rule = store.create_rule(
            name="R", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW, created_by=admin.id,
        )
        loaded = store.get_rule(rule.id)
        assert loaded is not None
        assert loaded.name == "R"

    def test_get_missing_returns_none(self, store):
        assert store.get_rule("nonexistent") is None

    def test_list_rules(self, store, principals):
        admin, _, _ = principals
        store.create_rule(name="R1", rule_type=RuleType.ACCESS, effect=RuleEffect.ALLOW, created_by=admin.id)
        store.create_rule(name="R2", rule_type=RuleType.SHARING, effect=RuleEffect.DENY, created_by=admin.id)
        assert len(store.list_rules()) == 2

    def test_list_by_type(self, store, principals):
        admin, _, _ = principals
        store.create_rule(name="R1", rule_type=RuleType.ACCESS, effect=RuleEffect.ALLOW, created_by=admin.id)
        store.create_rule(name="R2", rule_type=RuleType.SHARING, effect=RuleEffect.DENY, created_by=admin.id)
        assert len(store.list_rules(rule_type=RuleType.ACCESS)) == 1

    def test_list_by_effect(self, store, principals):
        admin, _, _ = principals
        store.create_rule(name="R1", rule_type=RuleType.ACCESS, effect=RuleEffect.ALLOW, created_by=admin.id)
        store.create_rule(name="R2", rule_type=RuleType.ACCESS, effect=RuleEffect.DENY, created_by=admin.id)
        assert len(store.list_rules(effect=RuleEffect.DENY)) == 1

    def test_update_rule_creates_version(self, store, principals):
        admin, _, _ = principals
        rule = store.create_rule(
            name="R", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW, priority=0,
            conditions={"action": ["read"]},
            created_by=admin.id,
        )
        updated = store.update_rule(
            rule.id, updated_by=admin.id,
            effect=RuleEffect.DENY, priority=10,
            change_reason="tightened security",
        )
        assert updated.effect == RuleEffect.DENY
        assert updated.priority == 10
        assert updated.current_version == 2

        versions = store.get_versions(rule.id)
        assert len(versions) == 2
        assert versions[0].version == 1
        assert versions[0].effect == RuleEffect.ALLOW
        assert versions[1].version == 2
        assert versions[1].effect == RuleEffect.DENY

    def test_archive_rule(self, store, principals):
        admin, _, _ = principals
        rule = store.create_rule(
            name="R", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW, created_by=admin.id,
        )
        store.bind_rule(rule.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)

        archived = store.archive_rule(rule.id, archived_by=admin.id)
        assert not archived.is_active

        # Bindings also archived
        bindings = store.get_bindings(rule.id, active_only=True)
        assert len(bindings) == 0

    def test_archive_excludes_from_list(self, store, principals):
        admin, _, _ = principals
        r = store.create_rule(
            name="R", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW, created_by=admin.id,
        )
        store.archive_rule(r.id, archived_by=admin.id)
        assert len(store.list_rules()) == 0


# -----------------------------------------------------------------------
# Bindings
# -----------------------------------------------------------------------

class TestBindings:

    def test_bind_and_get(self, store, principals):
        admin, _, _ = principals
        rule = store.create_rule(
            name="R", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW, created_by=admin.id,
        )
        binding = store.bind_rule(
            rule.id, target_type=BindingTargetType.SCOPE,
            target_id="scope-1", bound_by=admin.id,
        )
        assert binding.target_type == BindingTargetType.SCOPE
        assert binding.target_id == "scope-1"

        bindings = store.get_bindings(rule.id)
        assert len(bindings) == 1

    def test_unbind(self, store, principals):
        admin, _, _ = principals
        rule = store.create_rule(
            name="R", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW, created_by=admin.id,
        )
        store.bind_rule(
            rule.id, target_type=BindingTargetType.SCOPE,
            target_id="s1", bound_by=admin.id,
        )
        assert store.unbind_rule(
            rule.id, target_type=BindingTargetType.SCOPE, target_id="s1",
        )
        assert len(store.get_bindings(rule.id)) == 0

    def test_unbind_nonexistent_returns_false(self, store, principals):
        admin, _, _ = principals
        rule = store.create_rule(
            name="R", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW, created_by=admin.id,
        )
        assert not store.unbind_rule(
            rule.id, target_type=BindingTargetType.SCOPE, target_id="xxx",
        )

    def test_multiple_bindings(self, store, principals):
        admin, _, _ = principals
        rule = store.create_rule(
            name="R", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW, created_by=admin.id,
        )
        store.bind_rule(rule.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)
        store.bind_rule(rule.id, target_type=BindingTargetType.OBJECT_TYPE, target_id="Doc", bound_by=admin.id)

        bindings = store.get_bindings(rule.id)
        assert len(bindings) == 2

    def test_get_target_bindings(self, store, principals):
        admin, _, _ = principals
        r1 = store.create_rule(name="R1", rule_type=RuleType.ACCESS, effect=RuleEffect.ALLOW, created_by=admin.id)
        r2 = store.create_rule(name="R2", rule_type=RuleType.ACCESS, effect=RuleEffect.DENY, created_by=admin.id)
        store.bind_rule(r1.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)
        store.bind_rule(r2.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)

        target_bindings = store.get_target_bindings(BindingTargetType.SCOPE, "s1")
        assert len(target_bindings) == 2


# -----------------------------------------------------------------------
# Rule Engine evaluation
# -----------------------------------------------------------------------

class TestRuleEngineEvaluation:

    def test_default_deny_no_rules(self, engine, principals):
        """No rules → denied (default-deny)."""
        result = engine.evaluate(action="read", principal_id="user1", scope_id="s1")
        assert not result.allowed
        assert len(result.matching_rules) == 0

    def test_allow_rule_permits(self, store, engine, principals):
        """Single ALLOW rule → allowed."""
        admin, _, _ = principals
        rule = store.create_rule(
            name="Allow read", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["read"]},
            created_by=admin.id,
        )
        store.bind_rule(rule.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)

        result = engine.evaluate(action="read", scope_id="s1")
        assert result.allowed
        assert len(result.allow_rules) == 1

    def test_deny_overrides_allow(self, store, engine, principals):
        """DENY always wins over ALLOW."""
        admin, _, _ = principals
        allow = store.create_rule(
            name="Allow read", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["read"]},
            created_by=admin.id,
        )
        deny = store.create_rule(
            name="Deny read", rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY,
            conditions={"action": ["read"]},
            priority=10,
            created_by=admin.id,
        )
        store.bind_rule(allow.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)
        store.bind_rule(deny.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)

        result = engine.evaluate(action="read", scope_id="s1")
        assert not result.allowed
        assert len(result.deny_rules) == 1
        assert len(result.allow_rules) == 1

    def test_non_matching_action_not_applied(self, store, engine, principals):
        """Rule with action condition only matches that action."""
        admin, _, _ = principals
        rule = store.create_rule(
            name="Allow read only", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["read"]},
            created_by=admin.id,
        )
        store.bind_rule(rule.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)

        result = engine.evaluate(action="delete", scope_id="s1")
        assert not result.allowed  # default-deny, rule doesn't match

    def test_conditions_filter_by_object_type(self, store, engine, principals):
        admin, _, _ = principals
        rule = store.create_rule(
            name="Allow read docs", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["read"], "object_type": "Document"},
            created_by=admin.id,
        )
        store.bind_rule(rule.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)

        # Matches
        assert engine.evaluate(action="read", scope_id="s1", object_type="Document").allowed
        # Doesn't match
        assert not engine.evaluate(action="read", scope_id="s1", object_type="Task").allowed

    def test_conditions_filter_by_principal_kind(self, store, engine, principals):
        admin, _, bot = principals
        rule = store.create_rule(
            name="Deny bots", rule_type=RuleType.CONSTRAINT,
            effect=RuleEffect.DENY,
            conditions={"action": ["delete"], "principal_kind": "bot"},
            created_by=admin.id,
        )
        store.bind_rule(rule.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)

        # Bot is denied
        assert not engine.evaluate(
            action="delete", scope_id="s1", principal_kind="bot",
        ).allowed

    def test_empty_conditions_matches_all(self, store, engine, principals):
        """Rule with no conditions matches any request in its bound scope."""
        admin, _, _ = principals
        rule = store.create_rule(
            name="Allow all", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={},
            created_by=admin.id,
        )
        store.bind_rule(rule.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)

        assert engine.evaluate(action="read", scope_id="s1").allowed
        assert engine.evaluate(action="delete", scope_id="s1").allowed

    def test_multiple_bindings_same_rule(self, store, engine, principals):
        """Rule bound to scope and object_type requires both to match."""
        admin, _, _ = principals
        rule = store.create_rule(
            name="Allow reads", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["read"]},
            created_by=admin.id,
        )
        store.bind_rule(rule.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)
        store.bind_rule(rule.id, target_type=BindingTargetType.OBJECT_TYPE, target_id="Doc", bound_by=admin.id)

        # Rule found via scope binding
        result = engine.evaluate(action="read", scope_id="s1", object_type="Doc")
        assert result.allowed

    def test_principal_binding(self, store, engine, principals):
        admin, user, _ = principals
        rule = store.create_rule(
            name="Allow user reads", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["read"]},
            created_by=admin.id,
        )
        store.bind_rule(rule.id, target_type=BindingTargetType.PRINCIPAL, target_id=user.id, bound_by=admin.id)

        assert engine.evaluate(action="read", principal_id=user.id).allowed
        assert not engine.evaluate(action="read", principal_id="other").allowed

    def test_object_binding(self, store, engine, principals):
        admin, _, _ = principals
        rule = store.create_rule(
            name="Deny delete on obj-1", rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY,
            conditions={"action": ["delete"]},
            created_by=admin.id,
        )
        store.bind_rule(rule.id, target_type=BindingTargetType.OBJECT, target_id="obj-1", bound_by=admin.id)

        assert not engine.evaluate(action="delete", object_id="obj-1").allowed
        # Different object → no deny rule, but still default-deny
        assert not engine.evaluate(action="delete", object_id="obj-2").allowed

    def test_archived_rule_not_evaluated(self, store, engine, principals):
        admin, _, _ = principals
        rule = store.create_rule(
            name="Allow", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={},
            created_by=admin.id,
        )
        store.bind_rule(rule.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)
        store.archive_rule(rule.id, archived_by=admin.id)

        result = engine.evaluate(action="read", scope_id="s1")
        assert not result.allowed  # rule is archived → default-deny

    def test_archived_binding_not_used(self, store, engine, principals):
        admin, _, _ = principals
        rule = store.create_rule(
            name="Allow", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={},
            created_by=admin.id,
        )
        store.bind_rule(rule.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)
        store.unbind_rule(rule.id, target_type=BindingTargetType.SCOPE, target_id="s1")

        result = engine.evaluate(action="read", scope_id="s1")
        assert not result.allowed


# -----------------------------------------------------------------------
# Complex scenarios
# -----------------------------------------------------------------------

class TestComplexScenarios:

    def test_deny_wins_across_targets(self, store, engine, principals):
        """DENY on principal overrides ALLOW on scope."""
        admin, user, _ = principals
        allow_rule = store.create_rule(
            name="Allow scope", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW, conditions={"action": ["read"]},
            created_by=admin.id,
        )
        deny_rule = store.create_rule(
            name="Deny user", rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY, conditions={"action": ["read"]},
            priority=10, created_by=admin.id,
        )
        store.bind_rule(allow_rule.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)
        store.bind_rule(deny_rule.id, target_type=BindingTargetType.PRINCIPAL, target_id=user.id, bound_by=admin.id)

        result = engine.evaluate(action="read", scope_id="s1", principal_id=user.id)
        assert not result.allowed

    def test_multiple_allows_one_deny(self, store, engine, principals):
        admin, _, _ = principals
        # 3 ALLOW rules
        for i in range(3):
            r = store.create_rule(
                name=f"Allow {i}", rule_type=RuleType.ACCESS,
                effect=RuleEffect.ALLOW, conditions={"action": ["read"]},
                created_by=admin.id,
            )
            store.bind_rule(r.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)

        # 1 DENY rule
        deny = store.create_rule(
            name="Deny", rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY, conditions={"action": ["read"]},
            priority=100, created_by=admin.id,
        )
        store.bind_rule(deny.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)

        result = engine.evaluate(action="read", scope_id="s1")
        assert not result.allowed
        assert len(result.deny_rules) == 1
        assert len(result.allow_rules) == 3

    def test_scoped_allow_different_scope_denied(self, store, engine, principals):
        """Allow bound to s1 doesn't apply in s2."""
        admin, _, _ = principals
        rule = store.create_rule(
            name="Allow in s1", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW, conditions={},
            created_by=admin.id,
        )
        store.bind_rule(rule.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)

        assert engine.evaluate(action="read", scope_id="s1").allowed
        assert not engine.evaluate(action="read", scope_id="s2").allowed
