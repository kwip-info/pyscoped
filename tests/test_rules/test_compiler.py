"""Tests for RuleCompiler and CompiledRuleSet."""

import pytest

from scoped.identity.principal import PrincipalStore
from scoped.rules.compiler import RuleCompiler
from scoped.rules.engine import RuleStore
from scoped.rules.models import BindingTargetType, RuleEffect, RuleType


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    admin = store.create_principal(kind="user", display_name="Admin", principal_id="admin")
    return admin


@pytest.fixture
def store(sqlite_backend):
    return RuleStore(sqlite_backend)


@pytest.fixture
def compiler(sqlite_backend):
    return RuleCompiler(sqlite_backend)


class TestCompiledRuleSet:

    def test_compile_empty(self, compiler):
        ruleset = compiler.compile()
        assert ruleset.size == 0

    def test_compile_with_rules(self, store, compiler, principals):
        admin = principals
        r1 = store.create_rule(
            name="R1", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW, created_by=admin.id,
        )
        r2 = store.create_rule(
            name="R2", rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY, priority=10, created_by=admin.id,
        )
        store.bind_rule(r1.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)
        store.bind_rule(r2.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)

        ruleset = compiler.compile()
        assert ruleset.size == 2

    def test_lookup_by_target(self, store, compiler, principals):
        admin = principals
        r1 = store.create_rule(
            name="R1", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW, priority=0, created_by=admin.id,
        )
        r2 = store.create_rule(
            name="R2", rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY, priority=10, created_by=admin.id,
        )
        store.bind_rule(r1.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)
        store.bind_rule(r2.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)

        ruleset = compiler.compile()
        rules = ruleset.lookup(BindingTargetType.SCOPE, "s1")
        assert len(rules) == 2
        # Should be sorted by priority (highest first)
        assert rules[0].priority >= rules[1].priority

    def test_lookup_different_target(self, store, compiler, principals):
        admin = principals
        r = store.create_rule(
            name="R", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW, created_by=admin.id,
        )
        store.bind_rule(r.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)

        ruleset = compiler.compile()
        assert len(ruleset.lookup(BindingTargetType.SCOPE, "s2")) == 0

    def test_compile_filter_by_type(self, store, compiler, principals):
        admin = principals
        r1 = store.create_rule(
            name="Access", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW, created_by=admin.id,
        )
        r2 = store.create_rule(
            name="Sharing", rule_type=RuleType.SHARING,
            effect=RuleEffect.DENY, created_by=admin.id,
        )
        store.bind_rule(r1.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)
        store.bind_rule(r2.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)

        ruleset = compiler.compile(rule_type="access")
        assert ruleset.size == 1

    def test_archived_rules_excluded(self, store, compiler, principals):
        admin = principals
        r = store.create_rule(
            name="R", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW, created_by=admin.id,
        )
        store.bind_rule(r.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=admin.id)
        store.archive_rule(r.id, archived_by=admin.id)

        ruleset = compiler.compile()
        assert ruleset.size == 0
