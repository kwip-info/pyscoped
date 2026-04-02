"""Shared fixtures for contrib adapter tests."""

from __future__ import annotations

import pytest

from scoped.client import ScopedClient
from scoped.contrib._base import build_services
from scoped.storage.sa_sqlite import SASQLiteBackend as SQLiteBackend


@pytest.fixture
def contrib_backend():
    """In-memory SQLite backend for adapter tests."""
    backend = SQLiteBackend(":memory:")
    backend.initialize()
    yield backend
    backend.close()


@pytest.fixture
def contrib_client(contrib_backend):
    """ScopedClient for adapter tests."""
    return ScopedClient(backend=contrib_backend)


@pytest.fixture
def services(contrib_backend):
    """Full service dict for adapter tests (legacy compat)."""
    return build_services(contrib_backend)
