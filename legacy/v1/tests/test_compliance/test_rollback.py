"""Tests for RollbackVerifier."""

from __future__ import annotations

from scoped.testing.rollback import RollbackVerifier
from scoped.types import generate_id, now_utc


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


class TestRollbackVerifier:
    def test_verify_create_rollback(self, sqlite_backend):
        pid = _setup_principal(sqlite_backend)
        verifier = RollbackVerifier(sqlite_backend)

        check = verifier.verify_create_rollback(principal_id=pid)

        assert check.passed
        assert check.mutation_type == "create"

    def test_verify_update_rollback(self, sqlite_backend):
        pid = _setup_principal(sqlite_backend)
        verifier = RollbackVerifier(sqlite_backend)

        check = verifier.verify_update_rollback(principal_id=pid)

        assert check.passed
        assert check.mutation_type == "update"

    def test_verify_tombstone_rollback(self, sqlite_backend):
        pid = _setup_principal(sqlite_backend)
        verifier = RollbackVerifier(sqlite_backend)

        check = verifier.verify_tombstone_rollback(principal_id=pid)

        assert check.passed
        assert check.mutation_type == "tombstone"

    def test_verify_all(self, sqlite_backend):
        pid = _setup_principal(sqlite_backend)
        verifier = RollbackVerifier(sqlite_backend)

        result = verifier.verify_all(principal_id=pid)

        assert result.passed
        assert len(result.checks) == 3
        assert len(result.failed) == 0

    def test_verify_all_properties(self, sqlite_backend):
        pid = _setup_principal(sqlite_backend)
        verifier = RollbackVerifier(sqlite_backend)

        result = verifier.verify_all(principal_id=pid)

        assert all(c.mutation_type in ("create", "update", "tombstone") for c in result.checks)
