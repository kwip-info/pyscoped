"""Tests for ComplianceAuditor."""

from __future__ import annotations

from scoped.audit.writer import AuditWriter
from scoped.objects.manager import ScopedManager
from scoped.testing.auditor import ComplianceAuditor
from scoped.types import ActionType, generate_id, now_utc


def _setup_principal(backend) -> str:
    pid = generate_id()
    ts = now_utc().isoformat()
    reg_id = generate_id()
    backend.execute(
        "INSERT OR IGNORE INTO registry_entries "
        "(id, urn, kind, namespace, name, registered_at, registered_by) "
        "VALUES (?, ?, 'MODEL', 'test', 'stub', ?, 'system')",
        (reg_id, f"scoped:MODEL:test:stub_{pid[:8]}:1", ts),
    )
    backend.execute(
        "INSERT INTO principals (id, kind, display_name, registry_entry_id, created_at) "
        "VALUES (?, 'user', 'Test User', ?, ?)",
        (pid, reg_id, ts),
    )
    return pid


class TestRegistryCompleteness:
    def test_passes_when_all_principals_have_entries(self, sqlite_backend):
        _setup_principal(sqlite_backend)

        auditor = ComplianceAuditor(sqlite_backend)
        result = auditor.check_registry_completeness()

        assert result.passed
        assert len(result.violations) == 0

    def test_fails_for_orphaned_principals(self, sqlite_backend):
        # Create a valid principal, then orphan it
        pid = _setup_principal(sqlite_backend)
        row = sqlite_backend.fetch_one(
            "SELECT registry_entry_id FROM principals WHERE id = ?", (pid,),
        )
        reg_id = row["registry_entry_id"]

        # Temporarily disable FK to simulate data corruption
        sqlite_backend.execute("PRAGMA foreign_keys = OFF", ())
        sqlite_backend.execute("DELETE FROM registry_entries WHERE id = ?", (reg_id,))
        sqlite_backend.execute("PRAGMA foreign_keys = ON", ())

        auditor = ComplianceAuditor(sqlite_backend)
        result = auditor.check_registry_completeness()

        assert not result.passed
        assert len(result.violations) == 1
        assert pid in result.violations[0]


class TestTraceIntegrity:
    def test_passes_with_valid_chain(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        writer = AuditWriter(sqlite_backend)

        writer.record(
            actor_id=user, action=ActionType.CREATE,
            target_type="test", target_id="t1",
        )
        writer.record(
            actor_id=user, action=ActionType.UPDATE,
            target_type="test", target_id="t1",
        )

        auditor = ComplianceAuditor(sqlite_backend)
        result = auditor.check_trace_integrity()

        assert result.passed

    def test_passes_with_empty_chain(self, sqlite_backend):
        auditor = ComplianceAuditor(sqlite_backend)
        result = auditor.check_trace_integrity()

        assert result.passed

    def test_fails_with_tampered_chain(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        writer = AuditWriter(sqlite_backend)

        writer.record(
            actor_id=user, action=ActionType.CREATE,
            target_type="test", target_id="t1",
        )
        writer.record(
            actor_id=user, action=ActionType.UPDATE,
            target_type="test", target_id="t1",
        )

        # Tamper with a hash
        sqlite_backend.execute(
            "UPDATE audit_trail SET hash = 'tampered' WHERE sequence = 2",
            (),
        )

        auditor = ComplianceAuditor(sqlite_backend)
        result = auditor.check_trace_integrity()

        assert not result.passed
        assert "broken" in result.details.lower()


class TestIsolationIntegrity:
    def test_passes_with_valid_objects(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        manager = ScopedManager(sqlite_backend)
        manager.create(object_type="doc", owner_id=user, data={"x": 1})

        auditor = ComplianceAuditor(sqlite_backend)
        result = auditor.check_isolation_integrity()

        assert result.passed

    def test_fails_with_orphaned_objects(self, sqlite_backend):
        # Create a valid object, then delete its owner principal
        pid = _setup_principal(sqlite_backend)
        manager = ScopedManager(sqlite_backend)
        obj, _ = manager.create(object_type="doc", owner_id=pid, data={"x": 1})

        # Temporarily disable FK to simulate data corruption
        sqlite_backend.execute("PRAGMA foreign_keys = OFF", ())
        sqlite_backend.execute("DELETE FROM principals WHERE id = ?", (pid,))
        sqlite_backend.execute("PRAGMA foreign_keys = ON", ())

        auditor = ComplianceAuditor(sqlite_backend)
        result = auditor.check_isolation_integrity()

        assert not result.passed
        assert len(result.violations) == 1


class TestRuleConsistency:
    def test_passes_with_no_rules(self, sqlite_backend):
        auditor = ComplianceAuditor(sqlite_backend)
        result = auditor.check_rule_consistency()

        assert result.passed

    def test_warns_on_contradictory_rules(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)

        from scoped.rules.engine import RuleStore
        from scoped.rules.models import BindingTargetType, RuleEffect, RuleType

        store = RuleStore(sqlite_backend)

        r1 = store.create_rule(
            name="allow-read",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            priority=5,
            created_by=user,
        )
        r2 = store.create_rule(
            name="deny-read",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY,
            priority=5,
            created_by=user,
        )

        # Bind both to the same target
        store.bind_rule(r1.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=user)
        store.bind_rule(r2.id, target_type=BindingTargetType.SCOPE, target_id="s1", bound_by=user)

        auditor = ComplianceAuditor(sqlite_backend)
        result = auditor.check_rule_consistency()

        # Rule consistency still passes (DENY wins), but has warnings
        assert result.passed
        assert len(result.warnings) == 1
        assert "Contradictory" in result.warnings[0]


class TestScopeBoundaries:
    def test_passes_with_clean_scopes(self, sqlite_backend):
        auditor = ComplianceAuditor(sqlite_backend)
        result = auditor.check_scope_boundaries()

        assert result.passed


class TestSecretHygiene:
    def test_passes_with_no_secrets(self, sqlite_backend):
        auditor = ComplianceAuditor(sqlite_backend)
        result = auditor.check_secret_hygiene()

        assert result.passed


class TestRunAll:
    def test_run_all_on_clean_system(self, sqlite_backend):
        _setup_principal(sqlite_backend)

        auditor = ComplianceAuditor(sqlite_backend)
        result = auditor.run_all()

        assert result.passed
        assert result.total_checks >= 6
        assert result.failed_count == 0

    def test_run_all_detects_issues(self, sqlite_backend):
        # Create a valid principal, then orphan it
        pid = _setup_principal(sqlite_backend)
        row = sqlite_backend.fetch_one(
            "SELECT registry_entry_id FROM principals WHERE id = ?", (pid,),
        )
        sqlite_backend.execute("PRAGMA foreign_keys = OFF", ())
        sqlite_backend.execute(
            "DELETE FROM registry_entries WHERE id = ?", (row["registry_entry_id"],),
        )
        sqlite_backend.execute("PRAGMA foreign_keys = ON", ())

        auditor = ComplianceAuditor(sqlite_backend)
        result = auditor.run_all()

        assert not result.passed
        assert result.failed_count >= 1
