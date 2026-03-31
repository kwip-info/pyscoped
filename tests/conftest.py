"""Shared test fixtures for the Scoped framework."""

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


@pytest.fixture(autouse=True)
def _reset_global_state():
    """Reset global singletons between tests."""
    yield
    reset_global_registry()
    CustomKind.reset()
