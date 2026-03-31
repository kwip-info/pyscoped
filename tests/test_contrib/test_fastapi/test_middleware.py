"""Tests for FastAPI ScopedContextMiddleware."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


class TestScopedContextMiddleware:
    def test_sets_context_from_header(self, client, fastapi_user):
        response = client.get(
            "/test", headers={"x-scoped-principal-id": fastapi_user.id}
        )
        assert response.status_code == 200
        assert response.json()["principal_id"] == fastapi_user.id

    def test_no_header_no_context(self, client):
        response = client.get("/test")
        assert response.status_code == 200
        assert response.json()["principal_id"] is None

    def test_unknown_principal_no_context(self, client):
        response = client.get(
            "/test", headers={"x-scoped-principal-id": "nonexistent"}
        )
        assert response.status_code == 200
        assert response.json()["principal_id"] is None

    def test_context_cleaned_up(self, client, fastapi_user):
        from scoped.identity.context import ScopedContext

        client.get("/test", headers={"x-scoped-principal-id": fastapi_user.id})
        assert ScopedContext.current_or_none() is None
