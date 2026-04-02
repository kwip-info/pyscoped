"""Tests for HealthChecker."""

from __future__ import annotations

from scoped.audit.writer import AuditWriter
from scoped.testing.health import HealthChecker, HealthStatus
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


class TestDBConnectivity:
    def test_passes(self, sqlite_backend):
        checker = HealthChecker(sqlite_backend)
        check = checker.check_db_connectivity()

        assert check.passed
        assert "responsive" in check.detail.lower()


class TestSchemaTables:
    def test_passes_with_complete_schema(self, sqlite_backend):
        checker = HealthChecker(sqlite_backend)
        check = checker.check_schema_tables()

        assert check.passed
        assert "required tables present" in check.detail


class TestAuditChain:
    def test_passes_with_empty_chain(self, sqlite_backend):
        checker = HealthChecker(sqlite_backend)
        check = checker.check_audit_chain()

        assert check.passed

    def test_passes_with_valid_entries(self, sqlite_backend):
        pid = _setup_principal(sqlite_backend)
        writer = AuditWriter(sqlite_backend)
        writer.record(
            actor_id=pid, action=ActionType.CREATE,
            target_type="test", target_id="t1",
        )

        checker = HealthChecker(sqlite_backend)
        check = checker.check_audit_chain()

        assert check.passed
        assert "1 entries" in check.detail

    def test_fails_with_tampered_chain(self, sqlite_backend):
        pid = _setup_principal(sqlite_backend)
        writer = AuditWriter(sqlite_backend)
        writer.record(
            actor_id=pid, action=ActionType.CREATE,
            target_type="test", target_id="t1",
        )
        writer.record(
            actor_id=pid, action=ActionType.UPDATE,
            target_type="test", target_id="t1",
        )

        # Tamper
        sqlite_backend.execute(
            "UPDATE audit_trail SET hash = 'bad' WHERE sequence = 2", (),
        )

        checker = HealthChecker(sqlite_backend)
        check = checker.check_audit_chain()

        assert not check.passed


class TestMigrationState:
    def test_passes_with_or_without_migration_table(self, sqlite_backend):
        checker = HealthChecker(sqlite_backend)
        check = checker.check_migration_state()

        assert check.passed


class TestCheckAll:
    def test_all_healthy(self, sqlite_backend):
        checker = HealthChecker(sqlite_backend)
        status = checker.check_all()

        assert status.healthy
        assert len(status.checks) >= 4

    def test_health_status_add(self):
        from scoped.testing.health import HealthCheck

        status = HealthStatus()
        status.add(HealthCheck(name="test", passed=True, detail="ok"))

        assert status.healthy
        assert "test" in status.checks

    def test_health_status_unhealthy(self):
        from scoped.testing.health import HealthCheck

        status = HealthStatus()
        status.add(HealthCheck(name="good", passed=True))
        status.add(HealthCheck(name="bad", passed=False))

        assert not status.healthy
