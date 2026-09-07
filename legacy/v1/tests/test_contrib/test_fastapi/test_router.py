"""Tests for FastAPI admin router."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


class TestHealthRoute:
    def test_health_endpoint(self, client, fastapi_user):
        response = client.get(
            "/scoped/health",
            headers={"x-scoped-principal-id": fastapi_user.id},
        )
        assert response.status_code == 200
        data = response.json()
        assert "healthy" in data
        assert "checks" in data

    def test_health_returns_check_details(self, client, fastapi_user):
        response = client.get(
            "/scoped/health",
            headers={"x-scoped-principal-id": fastapi_user.id},
        )
        data = response.json()
        for check in data["checks"].values():
            assert "passed" in check
            assert "detail" in check


class TestAuditRoute:
    def test_audit_endpoint_empty(self, client, fastapi_user):
        response = client.get(
            "/scoped/audit",
            headers={"x-scoped-principal-id": fastapi_user.id},
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_audit_with_entries(self, client, fastapi_user, fastapi_backend):
        from scoped.audit.writer import AuditWriter
        from scoped.types import ActionType

        writer = AuditWriter(fastapi_backend)
        writer.record(
            actor_id=fastapi_user.id,
            action=ActionType.CREATE,
            target_type="test",
            target_id="t1",
        )

        response = client.get(
            "/scoped/audit",
            headers={"x-scoped-principal-id": fastapi_user.id},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert data[0]["action"] == "create"

    def test_audit_filter_by_actor(self, client, fastapi_user, fastapi_backend):
        from scoped.audit.writer import AuditWriter
        from scoped.types import ActionType

        writer = AuditWriter(fastapi_backend)
        writer.record(
            actor_id=fastapi_user.id,
            action=ActionType.CREATE,
            target_type="test",
            target_id="t2",
        )

        response = client.get(
            "/scoped/audit",
            params={"actor_id": fastapi_user.id},
            headers={"x-scoped-principal-id": fastapi_user.id},
        )
        assert response.status_code == 200
        for entry in response.json():
            assert entry["actor_id"] == fastapi_user.id
