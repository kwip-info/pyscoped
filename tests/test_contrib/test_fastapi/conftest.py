"""FastAPI adapter test fixtures."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from scoped.contrib.fastapi.middleware import ScopedContextMiddleware
from scoped.contrib.fastapi.router import router as scoped_router
from scoped.identity.context import ScopedContext
from scoped.storage.sa_sqlite import SASQLiteBackend as SQLiteBackend


@pytest.fixture
def fastapi_backend():
    backend = SQLiteBackend(":memory:")
    backend.initialize()
    yield backend
    backend.close()


@pytest.fixture
def fastapi_user(fastapi_backend):
    # Middleware init sets the global client, so use it
    import scoped
    from scoped.client import init

    init(backend=fastapi_backend)
    return scoped.principals.create("FastAPI User")


@pytest.fixture
def fastapi_app(fastapi_backend):
    app = FastAPI()
    app.add_middleware(ScopedContextMiddleware, backend=fastapi_backend)
    app.include_router(scoped_router)

    @app.get("/test")
    def test_endpoint():
        ctx = ScopedContext.current_or_none()
        if ctx:
            return {"principal_id": ctx.principal_id}
        return {"principal_id": None}

    return app


@pytest.fixture
def client(fastapi_app):
    return TestClient(fastapi_app)
