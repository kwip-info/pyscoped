"""Tests for ComplianceMiddleware (runtime enforcement)."""

from __future__ import annotations

import pytest

from scoped.audit.writer import AuditWriter
from scoped.exceptions import ComplianceViolation
from scoped.identity.context import ScopedContext
from scoped.identity.principal import Principal
from scoped.objects.manager import ScopedManager
from scoped.testing.middleware import ComplianceMiddleware
from scoped.types import ActionType, Lifecycle, Metadata, generate_id, now_utc


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


def _make_principal(pid: str) -> Principal:
    return Principal(
        id=pid, kind="user", display_name="Test",
        registry_entry_id="reg", created_at=now_utc(),
        created_by="system",
    )


class TestContextEnforcement:
    def test_passes_with_active_context(self, sqlite_backend):
        pid = _setup_principal(sqlite_backend)
        middleware = ComplianceMiddleware(sqlite_backend)

        principal = _make_principal(pid)
        with ScopedContext(principal):
            check = middleware.enforce_context()

        assert check.passed
        assert pid in check.detail

    def test_fails_without_context(self, sqlite_backend):
        middleware = ComplianceMiddleware(sqlite_backend)

        with pytest.raises(ComplianceViolation, match="without ScopedContext"):
            middleware.enforce_context()

    def test_no_raise_mode(self, sqlite_backend):
        middleware = ComplianceMiddleware(sqlite_backend, raise_on_violation=False)

        check = middleware.enforce_context()

        assert not check.passed
        assert len(middleware.violations) == 1


class TestTraceEnforcement:
    def test_passes_when_trace_exists(self, sqlite_backend):
        pid = _setup_principal(sqlite_backend)
        writer = AuditWriter(sqlite_backend)
        middleware = ComplianceMiddleware(sqlite_backend)

        entry = writer.record(
            actor_id=pid, action=ActionType.CREATE,
            target_type="doc", target_id="obj1",
        )

        check = middleware.enforce_trace(
            actor_id=pid, action=ActionType.CREATE, target_id="obj1",
        )

        assert check.passed

    def test_fails_when_trace_missing(self, sqlite_backend):
        middleware = ComplianceMiddleware(sqlite_backend)

        with pytest.raises(ComplianceViolation, match="without trace"):
            middleware.enforce_trace(
                actor_id="nobody", action=ActionType.CREATE, target_id="nothing",
            )

    def test_no_raise_mode_records_violation(self, sqlite_backend):
        middleware = ComplianceMiddleware(sqlite_backend, raise_on_violation=False)

        check = middleware.enforce_trace(
            actor_id="nobody", action=ActionType.CREATE, target_id="nothing",
        )

        assert not check.passed
        assert len(middleware.violations) == 1


class TestVersionIntegrity:
    def test_passes_with_correct_version(self, sqlite_backend):
        pid = _setup_principal(sqlite_backend)
        manager = ScopedManager(sqlite_backend)
        middleware = ComplianceMiddleware(sqlite_backend)

        obj, _ = manager.create(object_type="doc", owner_id=pid, data={"x": 1})

        check = middleware.enforce_version_integrity(obj.id, expected_version=1)
        assert check.passed

    def test_fails_with_wrong_version(self, sqlite_backend):
        pid = _setup_principal(sqlite_backend)
        manager = ScopedManager(sqlite_backend)
        middleware = ComplianceMiddleware(sqlite_backend)

        obj, _ = manager.create(object_type="doc", owner_id=pid, data={"x": 1})

        with pytest.raises(ComplianceViolation, match="Version integrity"):
            middleware.enforce_version_integrity(obj.id, expected_version=99)

    def test_passes_after_update(self, sqlite_backend):
        pid = _setup_principal(sqlite_backend)
        manager = ScopedManager(sqlite_backend)
        middleware = ComplianceMiddleware(sqlite_backend)

        obj, _ = manager.create(object_type="doc", owner_id=pid, data={"x": 1})
        manager.update(obj.id, principal_id=pid, data={"x": 2})

        check = middleware.enforce_version_integrity(obj.id, expected_version=2)
        assert check.passed

    def test_fails_for_nonexistent_object(self, sqlite_backend):
        middleware = ComplianceMiddleware(sqlite_backend)

        check = middleware.enforce_version_integrity("nonexistent", expected_version=1)
        assert not check.passed


class TestSecretLeakDetection:
    def test_passes_with_clean_state(self, sqlite_backend):
        middleware = ComplianceMiddleware(sqlite_backend)

        check = middleware.enforce_secret_not_in_state(
            {"data": "public info"},
            known_secret_values=["super_secret_key"],
        )

        assert check.passed

    def test_fails_when_secret_in_state(self, sqlite_backend):
        middleware = ComplianceMiddleware(sqlite_backend)

        with pytest.raises(ComplianceViolation, match="Secret leak"):
            middleware.enforce_secret_not_in_state(
                {"data": "contains super_secret_key here"},
                known_secret_values=["super_secret_key"],
            )

    def test_passes_with_no_secrets_to_check(self, sqlite_backend):
        middleware = ComplianceMiddleware(sqlite_backend)

        check = middleware.enforce_secret_not_in_state({"data": "anything"})
        assert check.passed

    def test_passes_with_none_state(self, sqlite_backend):
        middleware = ComplianceMiddleware(sqlite_backend)

        check = middleware.enforce_secret_not_in_state(
            None, known_secret_values=["secret"],
        )
        assert check.passed


class TestRevocationEnforcement:
    def test_passes_when_access_denied(self, sqlite_backend):
        pid_a = _setup_principal(sqlite_backend)
        pid_b = _setup_principal(sqlite_backend)
        manager = ScopedManager(sqlite_backend)
        middleware = ComplianceMiddleware(sqlite_backend)

        obj, _ = manager.create(object_type="doc", owner_id=pid_a, data={"x": 1})

        # user_b should not have access
        check = middleware.enforce_revocation(
            principal_id=pid_b, object_id=obj.id, manager=manager,
        )

        assert check.passed

    def test_fails_when_access_still_allowed(self, sqlite_backend):
        pid = _setup_principal(sqlite_backend)
        manager = ScopedManager(sqlite_backend)
        middleware = ComplianceMiddleware(sqlite_backend)

        obj, _ = manager.create(object_type="doc", owner_id=pid, data={"x": 1})

        # Owner still has access — this should fail the revocation check
        with pytest.raises(ComplianceViolation, match="Revocation not immediate"):
            middleware.enforce_revocation(
                principal_id=pid, object_id=obj.id, manager=manager,
            )


class TestMiddlewareState:
    def test_checks_accumulate(self, sqlite_backend):
        middleware = ComplianceMiddleware(sqlite_backend, raise_on_violation=False)

        middleware.enforce_context()
        middleware.enforce_context()

        assert len(middleware.checks) == 2

    def test_reset_clears_checks(self, sqlite_backend):
        middleware = ComplianceMiddleware(sqlite_backend, raise_on_violation=False)

        middleware.enforce_context()
        assert len(middleware.checks) == 1

        middleware.reset()
        assert len(middleware.checks) == 0

    def test_violations_property(self, sqlite_backend):
        middleware = ComplianceMiddleware(sqlite_backend, raise_on_violation=False)

        # One pass, one fail
        pid = _setup_principal(sqlite_backend)
        principal = _make_principal(pid)
        with ScopedContext(principal):
            middleware.enforce_context()  # pass
        middleware.enforce_context()  # fail (no context)

        assert len(middleware.checks) == 2
        assert len(middleware.violations) == 1
