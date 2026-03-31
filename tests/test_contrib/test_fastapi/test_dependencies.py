"""Tests for FastAPI dependency injection."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from scoped.contrib.fastapi.dependencies import get_principal, get_scoped_context, get_services
from scoped.contrib.fastapi.middleware import ScopedContextMiddleware
from scoped.identity.principal import PrincipalStore
from scoped.storage.sqlite import SQLiteBackend


@pytest.fixture
def dep_app():
    backend = SQLiteBackend(":memory:")
    backend.initialize()

    from scoped.contrib.fastapi import set_backend, reset_backend

    set_backend(backend)

    store = PrincipalStore(backend)
    user = store.create_principal(kind="user", display_name="Dep User")

    app = FastAPI()
    app.add_middleware(ScopedContextMiddleware, backend=backend)

    @app.get("/ctx")
    def ctx_endpoint(ctx=Depends(get_scoped_context)):
        return {"principal_id": ctx.principal_id}

    @app.get("/principal")
    def principal_endpoint(principal=Depends(get_principal)):
        return {"id": principal.id, "kind": principal.kind}

    @app.get("/services")
    def services_endpoint(svc=Depends(get_services)):
        return {"keys": sorted(svc.keys())}

    yield {"app": app, "client": TestClient(app), "user": user, "backend": backend}
    backend.close()
    reset_backend()


class TestGetScopedContext:
    def test_returns_context(self, dep_app):
        response = dep_app["client"].get(
            "/ctx", headers={"x-scoped-principal-id": dep_app["user"].id}
        )
        assert response.status_code == 200
        assert response.json()["principal_id"] == dep_app["user"].id

    def test_401_without_context(self, dep_app):
        response = dep_app["client"].get("/ctx")
        assert response.status_code == 401


class TestGetPrincipal:
    def test_returns_principal(self, dep_app):
        response = dep_app["client"].get(
            "/principal", headers={"x-scoped-principal-id": dep_app["user"].id}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == dep_app["user"].id
        assert data["kind"] == "user"


class TestGetServices:
    def test_returns_service_dict(self, dep_app):
        response = dep_app["client"].get(
            "/services", headers={"x-scoped-principal-id": dep_app["user"].id}
        )
        assert response.status_code == 200
        keys = response.json()["keys"]
        assert "manager" in keys
        assert "principals" in keys
        assert "audit_writer" in keys
        assert "health" in keys
