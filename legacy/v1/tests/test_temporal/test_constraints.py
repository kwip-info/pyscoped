"""Tests for rollback constraint checking."""

import pytest

from scoped.audit.models import TraceEntry
from scoped.audit.writer import AuditWriter
from scoped.exceptions import RollbackDeniedError
from scoped.identity.principal import PrincipalStore
from scoped.rules.engine import RuleEngine, RuleStore
from scoped.rules.models import BindingTargetType, RuleEffect, RuleType
from scoped.temporal.constraints import ConstraintCheck, RollbackConstraintChecker
from scoped.types import ActionType, now_utc


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    return store.create_principal(kind="user", display_name="Admin", principal_id="admin")


@pytest.fixture
def writer(sqlite_backend):
    return AuditWriter(sqlite_backend)


@pytest.fixture
def rule_store(sqlite_backend):
    return RuleStore(sqlite_backend)


@pytest.fixture
def rule_engine(sqlite_backend):
    return RuleEngine(sqlite_backend)


@pytest.fixture
def checker(sqlite_backend, rule_engine):
    return RollbackConstraintChecker(sqlite_backend, rule_engine=rule_engine)


@pytest.fixture
def checker_no_rules(sqlite_backend):
    return RollbackConstraintChecker(sqlite_backend)


class TestHardConstraints:

    def test_audit_trace_immutable(self, checker, writer, principals):
        entry = writer.record(
            actor_id="admin", action=ActionType.READ,
            target_type="audit", target_id="some-trace",
        )
        check = checker.check(entry, actor_id="admin")
        assert not check.permitted
        assert "immutable" in check.reason

    def test_trace_target_type_immutable(self, checker, writer, principals):
        entry = writer.record(
            actor_id="admin", action=ActionType.READ,
            target_type="trace", target_id="some-trace",
        )
        check = checker.check(entry, actor_id="admin")
        assert not check.permitted

    def test_object_target_type_allowed(self, checker_no_rules, writer, principals):
        entry = writer.record(
            actor_id="admin", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
            after_state={"v": 1},
        )
        check = checker_no_rules.check(entry, actor_id="admin")
        assert check.permitted


class TestRuleBasedConstraints:

    def test_no_rule_engine_permits(self, checker_no_rules, writer, principals):
        entry = writer.record(
            actor_id="admin", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
        )
        check = checker_no_rules.check(entry, actor_id="admin")
        assert check.permitted

    def test_deny_rule_blocks_rollback(
        self, sqlite_backend, rule_store, rule_engine, writer, principals,
    ):
        # Create a deny rule for rollback on scope s1
        rule = rule_store.create_rule(
            name="No rollback",
            rule_type=RuleType.CONSTRAINT,
            effect=RuleEffect.DENY,
            conditions={"action": ["rollback"]},
            created_by="admin",
        )
        rule_store.bind_rule(
            rule.id,
            target_type=BindingTargetType.SCOPE,
            target_id="s1",
            bound_by="admin",
        )

        entry = writer.record(
            actor_id="admin", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
            scope_id="s1",
        )

        checker = RollbackConstraintChecker(sqlite_backend, rule_engine=rule_engine)
        check = checker.check(entry, actor_id="admin")
        assert not check.permitted
        assert "denied by rule engine" in check.reason
        assert len(check.deny_rules) > 0

    def test_allow_rule_permits_rollback(
        self, sqlite_backend, rule_store, rule_engine, writer, principals,
    ):
        # Create an allow rule for rollback
        rule = rule_store.create_rule(
            name="Allow rollback",
            rule_type=RuleType.CONSTRAINT,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["rollback"]},
            created_by="admin",
        )
        rule_store.bind_rule(
            rule.id,
            target_type=BindingTargetType.SCOPE,
            target_id="s1",
            bound_by="admin",
        )

        entry = writer.record(
            actor_id="admin", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
            scope_id="s1",
        )

        checker = RollbackConstraintChecker(sqlite_backend, rule_engine=rule_engine)
        check = checker.check(entry, actor_id="admin")
        assert check.permitted


class TestCheckOrRaise:

    def test_raises_on_denied(self, checker, writer, principals):
        entry = writer.record(
            actor_id="admin", action=ActionType.READ,
            target_type="audit", target_id="t1",
        )
        with pytest.raises(RollbackDeniedError) as exc_info:
            checker.check_or_raise(entry, actor_id="admin")
        assert "trace_id" in exc_info.value.context

    def test_returns_on_permitted(self, checker_no_rules, writer, principals):
        entry = writer.record(
            actor_id="admin", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
        )
        result = checker_no_rules.check_or_raise(entry, actor_id="admin")
        assert result.permitted


class TestCheckMany:

    def test_checks_all_entries(self, checker, writer, principals):
        e1 = writer.record(
            actor_id="admin", action=ActionType.READ,
            target_type="audit", target_id="t1",
        )
        e2 = writer.record(
            actor_id="admin", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
        )
        results = checker.check_many([e1, e2], actor_id="admin")
        assert len(results) == 2
        assert not results[0].permitted  # audit is immutable
        assert results[1].permitted      # object is fine


class TestConstraintCheckBool:

    def test_bool_true(self, writer, principals):
        entry = writer.record(
            actor_id="admin", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
        )
        check = ConstraintCheck(permitted=True, reason="ok", trace_entry=entry)
        assert bool(check) is True

    def test_bool_false(self, writer, principals):
        entry = writer.record(
            actor_id="admin", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
        )
        check = ConstraintCheck(permitted=False, reason="denied", trace_entry=entry)
        assert bool(check) is False
