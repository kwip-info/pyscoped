"""MCP adapter test fixtures."""

from __future__ import annotations

import pytest

mcp = pytest.importorskip("mcp")

from scoped.client import ScopedClient
from scoped.contrib.mcp.server import create_scoped_server
from scoped.storage.sqlite import SQLiteBackend


@pytest.fixture
def mcp_backend():
    backend = SQLiteBackend(":memory:")
    backend.initialize()
    yield backend
    backend.close()


@pytest.fixture
def mcp_client(mcp_backend):
    return ScopedClient(backend=mcp_backend)


@pytest.fixture
def mcp_server(mcp_backend):
    return create_scoped_server(mcp_backend)


@pytest.fixture
def mcp_user(mcp_client):
    return mcp_client.principals.create("MCP User")
