"""MCP server exposing Scoped framework operations."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from scoped.contrib._base import build_services
from scoped.contrib.mcp.tools import register_tools
from scoped.contrib.mcp.resources import register_resources
from scoped.storage.interface import StorageBackend


def create_scoped_server(
    backend: StorageBackend,
    *,
    name: str = "scoped",
) -> FastMCP:
    """Create an MCP server with Scoped tools and resources.

    Args:
        backend: An initialized StorageBackend instance.
        name: Server name.

    Returns:
        A ``FastMCP`` instance ready to run.
    """
    server = FastMCP(name)
    services = build_services(backend)

    register_tools(server, services)
    register_resources(server, services)

    return server
