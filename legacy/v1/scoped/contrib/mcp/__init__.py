"""MCP (Model Context Protocol) adapter for the Scoped framework.

Exposes Scoped operations as MCP tools and resources so AI agents
can interact with the framework through a standard protocol.

Usage::

    from scoped.contrib.mcp.server import create_scoped_server
    from scoped.storage.sa_sqlite import SASQLiteBackend as SQLiteBackend

    backend = SQLiteBackend("app.db")
    backend.initialize()

    mcp = create_scoped_server(backend)
    mcp.run()
"""

from __future__ import annotations

try:
    import mcp  # noqa: F401
except ImportError as exc:
    raise ImportError(
        "scoped.contrib.mcp requires the MCP SDK. "
        "Install with: pip install scoped[mcp]"
    ) from exc
