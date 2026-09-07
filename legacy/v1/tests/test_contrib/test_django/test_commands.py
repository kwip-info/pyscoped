"""Tests for Django management commands."""

from __future__ import annotations

import pytest

django = pytest.importorskip("django")

from io import StringIO

from django.core.management import call_command

from scoped.contrib.django import get_backend, reset_backend


@pytest.fixture(autouse=True)
def _reset():
    reset_backend()
    yield
    reset_backend()


@pytest.fixture
def backend():
    b = get_backend()
    b.initialize()
    return b


class TestScopedHealth:
    def test_health_output(self, backend):
        out = StringIO()
        call_command("scoped_health", stdout=out)
        output = out.getvalue()

        assert "PASS" in output or "FAIL" in output

    def test_health_passes_on_fresh_db(self, backend):
        out = StringIO()
        call_command("scoped_health", stdout=out)
        output = out.getvalue()

        assert "health checks passed" in output.lower() or "PASS" in output


class TestScopedAudit:
    def test_audit_empty(self, backend):
        out = StringIO()
        call_command("scoped_audit", stdout=out)
        output = out.getvalue()

        assert "No audit entries" in output or "0 entries" in output.lower()

    def test_audit_with_entries(self, backend):
        from scoped.audit.writer import AuditWriter
        from scoped.identity.principal import PrincipalStore
        from scoped.types import ActionType

        principals = PrincipalStore(backend)
        user = principals.create_principal(kind="user", display_name="Cmd User")
        writer = AuditWriter(backend)
        writer.record(
            actor_id=user.id,
            action=ActionType.CREATE,
            target_type="test",
            target_id="t1",
        )

        out = StringIO()
        call_command("scoped_audit", stdout=out)
        output = out.getvalue()

        assert "create" in output.lower()
        assert "1 entries" in output.lower() or "entries shown" in output.lower()


class TestScopedCompliance:
    def test_compliance_passes_on_fresh_db(self, backend):
        from scoped.identity.principal import PrincipalStore

        principals = PrincipalStore(backend)
        principals.create_principal(kind="user", display_name="Compliance User")

        out = StringIO()
        call_command("scoped_compliance", stdout=out)
        output = out.getvalue()

        assert "COMPLIANCE REPORT" in output.upper() or "PASSED" in output

    def test_compliance_with_flags(self, backend):
        from scoped.identity.principal import PrincipalStore

        principals = PrincipalStore(backend)
        principals.create_principal(kind="user", display_name="Flag User")

        out = StringIO()
        call_command("scoped_compliance", "--no-health", stdout=out)
        output = out.getvalue()

        # Should still produce output even without health checks
        assert len(output) > 0
