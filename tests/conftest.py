"""Shared test fixtures for the Scoped framework."""

import os

import pytest

from scoped.registry.base import Registry, reset_global_registry
from scoped.registry.kinds import CustomKind
from scoped.storage.sqlite import SQLiteBackend


@pytest.fixture
def registry():
    """Fresh, isolated registry for each test."""
    reg = Registry()
    yield reg
    reg.clear()


@pytest.fixture
def sqlite_backend():
    """In-memory SQLite backend, initialized with schema."""
    backend = SQLiteBackend(":memory:")
    backend.initialize()
    yield backend
    backend.close()


def _make_postgres_backend():
    """Create a PostgresBackend from env, or return None if unavailable."""
    dsn = os.environ.get("PYSCOPED_TEST_PG_DSN")
    if not dsn:
        return None
    try:
        from scoped.storage.postgres import PostgresBackend
    except ImportError:
        return None
    backend = PostgresBackend(dsn, pool_min_size=1, pool_max_size=3)
    backend.initialize()
    return backend


def _clean_postgres(backend) -> None:
    """Drop all tables so the next test starts fresh."""
    tables = backend.fetch_all(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public'",
        (),
    )
    if tables:
        names = ", ".join(r["table_name"] for r in tables)
        backend.execute(f"DROP TABLE IF EXISTS {names} CASCADE")


@pytest.fixture(params=["sqlite", "postgres"])
def storage_backend(request):
    """Parametrized backend fixture — runs tests on both SQLite and Postgres."""
    if request.param == "sqlite":
        backend = SQLiteBackend(":memory:")
        backend.initialize()
        yield backend
        backend.close()
    else:
        backend = _make_postgres_backend()
        if backend is None:
            pytest.skip("PYSCOPED_TEST_PG_DSN not set or psycopg not installed")
        yield backend
        _clean_postgres(backend)
        backend.close()


@pytest.fixture(autouse=True)
def _reset_global_state():
    """Reset global singletons between tests."""
    yield
    reset_global_registry()
    CustomKind.reset()
