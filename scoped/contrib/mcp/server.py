"""MCP server exposing Scoped framework operations.

Creates an MCP server that AI agents can use to interact with
pyscoped-managed data. Tools provide CRUD on objects, principals,
scopes, and audit trail queries.

Usage::

    from scoped.contrib.mcp.server import create_scoped_server
    from scoped.storage.sa_sqlite import SASQLiteBackend as SQLiteBackend

    backend = SQLiteBackend("app.db")
    backend.initialize()
    mcp = create_scoped_server(backend)
    mcp.run()

Or with a ``ScopedClient``::

    import scoped
    from scoped.contrib.mcp.server import create_scoped_server

    client = scoped.init(database_url="postgresql://...")
    mcp = create_scoped_server(client=client)
    mcp.run()
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from scoped.contrib.mcp.tools import register_tools
from scoped.contrib.mcp.resources import register_resources
from scoped.storage.interface import StorageBackend


def create_scoped_server(
    backend: StorageBackend | None = None,
    *,
    client: Any | None = None,
    name: str = "scoped",
) -> FastMCP:
    """Create an MCP server with Scoped tools and resources.

    Args:
        backend: An initialized ``StorageBackend`` instance. A
                 ``ScopedClient`` is created from it automatically.
        client: A pre-built ``ScopedClient``. If provided, *backend*
                is ignored.
        name: Server name.

    Returns:
        A ``FastMCP`` instance ready to run.
    """
    if client is None:
        from scoped.client import ScopedClient

        if backend is None:
            raise ValueError("Either backend or client must be provided")
        client = ScopedClient(backend=backend)

    server = FastMCP(name)

    register_tools(server, client)
    register_resources(server, client)

    return server
