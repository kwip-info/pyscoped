"""Tests for Flask ScopedExtension."""

from __future__ import annotations

import pytest

pytest.importorskip("flask")


class TestScopedExtension:
    def test_sets_context_from_header(self, flask_client, flask_user):
        response = flask_client.get(
            "/test", headers={"X-Scoped-Principal-Id": flask_user.id}
        )
        assert response.status_code == 200
        assert response.get_json()["principal_id"] == flask_user.id

    def test_no_header_no_context(self, flask_client):
        response = flask_client.get("/test")
        assert response.status_code == 200
        assert response.get_json()["principal_id"] is None

    def test_unknown_principal_no_context(self, flask_client):
        response = flask_client.get(
            "/test", headers={"X-Scoped-Principal-Id": "nonexistent"}
        )
        assert response.status_code == 200
        assert response.get_json()["principal_id"] is None

    def test_exempt_path_skips(self, flask_client, flask_user):
        response = flask_client.get(
            "/exempt/test", headers={"X-Scoped-Principal-Id": flask_user.id}
        )
        assert response.status_code == 200
        assert response.get_json()["exempt"] is True

    def test_context_cleaned_up(self, flask_client, flask_user):
        from scoped.identity.context import ScopedContext

        flask_client.get("/test", headers={"X-Scoped-Principal-Id": flask_user.id})
        assert ScopedContext.current_or_none() is None

    def test_extension_registered(self, flask_app):
        assert "scoped" in flask_app["app"].extensions

    def test_services_available(self, flask_app):
        ext = flask_app["ext"]
        assert ext.backend is not None
        assert "manager" in ext.services
        assert "principals" in ext.services
        assert "health" in ext.services

    def test_custom_resolver(self, flask_app, flask_user):
        flask_app["app"].config["SCOPED_PRINCIPAL_RESOLVER"] = lambda req: flask_user

        client = flask_app["app"].test_client()
        response = client.get("/test")
        assert response.get_json()["principal_id"] == flask_user.id

        del flask_app["app"].config["SCOPED_PRINCIPAL_RESOLVER"]


class TestFlaskAdmin:
    def test_health_endpoint(self, flask_client, flask_user):
        response = flask_client.get(
            "/scoped/health",
            headers={"X-Scoped-Principal-Id": flask_user.id},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert "healthy" in data
        assert "checks" in data

    def test_audit_endpoint_empty(self, flask_client, flask_user):
        response = flask_client.get(
            "/scoped/audit",
            headers={"X-Scoped-Principal-Id": flask_user.id},
        )
        assert response.status_code == 200
        assert isinstance(response.get_json(), list)

    def test_audit_with_entries(self, flask_client, flask_user, flask_app):
        from scoped.audit.writer import AuditWriter
        from scoped.types import ActionType

        writer = AuditWriter(flask_app["ext"].backend)
        writer.record(
            actor_id=flask_user.id,
            action=ActionType.CREATE,
            target_type="test",
            target_id="t1",
        )

        response = flask_client.get(
            "/scoped/audit",
            headers={"X-Scoped-Principal-Id": flask_user.id},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert len(data) >= 1
        assert data[0]["action"] == "create"
