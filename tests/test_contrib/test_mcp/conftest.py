"""MCP adapter test fixtures."""

from __future__ import annotations

import pytest

mcp = pytest.importorskip("mcp")

from scoped.contrib._base import build_services
from scoped.contrib.mcp.server import create_scoped_server
from scoped.identity.principal import PrincipalStore
from scoped.storage.sqlite import SQLiteBackend


@pytest.fixture
def mcp_backend():
    backend = SQLiteBackend(":memory:")
    backend.initialize()
    yield backend
    backend.close()


@pytest.fixture
def mcp_services(mcp_backend):
    return build_services(mcp_backend)


@pytest.fixture
def mcp_server(mcp_backend):
    return create_scoped_server(mcp_backend)


@pytest.fixture
def mcp_user(mcp_backend):
    store = PrincipalStore(mcp_backend)
    return store.create_principal(kind="user", display_name="MCP User")
