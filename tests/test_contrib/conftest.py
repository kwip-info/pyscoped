"""Shared fixtures for contrib adapter tests."""

from __future__ import annotations

import pytest

from scoped.contrib._base import build_services
from scoped.storage.sqlite import SQLiteBackend


@pytest.fixture
def contrib_backend():
    """In-memory SQLite backend for adapter tests."""
    backend = SQLiteBackend(":memory:")
    backend.initialize()
    yield backend
    backend.close()


@pytest.fixture
def services(contrib_backend):
    """Full service dict for adapter tests."""
    return build_services(contrib_backend)
