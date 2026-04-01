---
title: MCP Server Integration
description: Expose pyscoped operations as an MCP (Model Context Protocol) server so AI assistants like Claude Desktop can manage principals, objects, scopes, and audit logs through tool calls and resource reads.
category: integrations
---

# MCP Server Integration

pyscoped can be exposed as a Model Context Protocol (MCP) server, allowing AI
assistants and MCP-compatible clients to interact with your scoped data through
structured tool calls and resource URIs.

## What is MCP?

The [Model Context Protocol](https://modelcontextprotocol.io) (MCP) is an open
standard that defines how AI applications communicate with external data sources
and services. An MCP server exposes **tools** (callable operations) and
**resources** (readable data endpoints) that clients can discover and invoke.

By running pyscoped as an MCP server, you enable AI assistants such as Claude
Desktop, IDE extensions, and custom agents to:

- Create and manage principals, objects, and scopes
- Query audit logs
- Monitor system health

All interactions go through the pyscoped client, so every operation respects
your configured backend, permissions, and audit trail.

## Installation

```bash
pip install pyscoped[mcp]
```

This installs pyscoped along with the MCP SDK and server dependencies.

## Quick Start

```python
from scoped import ScopedClient
from scoped.contrib.mcp import create_scoped_server

# Initialize a pyscoped client with your backend.
client = ScopedClient(
    database_url="postgresql://localhost/mydb",
    api_key="sk-scoped-...",
)

# Create the MCP server.
server = create_scoped_server(client)

# Run the server (stdio transport by default).
server.run()
```

## create_scoped_server

```python
create_scoped_server(client: ScopedClient) -> MCPServer
```

Accepts a configured `ScopedClient` instance and returns an MCP server with
all pyscoped tools and resources registered. The server can be run with any
MCP-compatible transport (stdio, HTTP, WebSocket).

## Available Tools

The MCP server exposes six tools that MCP clients can call.

### create_principal

Creates a new principal in the backend.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `name` | string | Yes | Display name for the principal. |
| `metadata` | object | No | Arbitrary metadata to attach to the principal. |

**Returns:** The created principal object with its generated ID.

```json
{
    "id": "prin_a1b2c3",
    "name": "Alice",
    "metadata": {"role": "admin"},
    "created_at": "2026-03-15T10:30:00Z"
}
```

### create_object

Creates a new scoped object.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `kind` | string | Yes | The object type (e.g., "document", "task"). |
| `data` | object | No | The object payload. |
| `scope_id` | string | No | Scope to associate the object with. |

**Returns:** The created object with its generated ID.

### get_object

Retrieves a single object by ID.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `object_id` | string | Yes | The ID of the object to retrieve. |

**Returns:** The full object including its data, kind, and metadata.

### create_scope

Creates a new scope and optionally adds principals as members.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `name` | string | Yes | Display name for the scope. |
| `principal_ids` | array | No | List of principal IDs to add as initial members. |
| `metadata` | object | No | Arbitrary metadata for the scope. |

**Returns:** The created scope object.

### list_audit

Retrieves audit log entries with optional filtering.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `limit` | integer | No | Maximum number of entries to return. Defaults to 50. |
| `principal_id` | string | No | Filter entries by principal. |
| `action` | string | No | Filter entries by action type. |

**Returns:** An array of audit log entries.

### health_check

Runs a health check against the backend and returns diagnostic information.

| Parameter | Type | Required | Description |
|---|---|---|---|
| *(none)* | | | This tool takes no parameters. |

**Returns:** Backend status, connection health, and aggregate counts.

```json
{
    "status": "healthy",
    "backend": "PostgreSQLBackend",
    "principals": 142,
    "objects": 8491,
    "scopes": 37
}
```

## Available Resources

The MCP server exposes three resources that clients can read.

### scoped://principals

Returns a list of all principals in the system.

```json
{
    "principals": [
        {"id": "prin_a1b2c3", "name": "Alice", "created_at": "2026-03-15T10:30:00Z"},
        {"id": "prin_d4e5f6", "name": "Bob", "created_at": "2026-03-16T08:15:00Z"}
    ]
}
```

### scoped://health

Returns the current health status of the backend. Equivalent to the
`health_check` tool but accessible as a readable resource.

```json
{
    "status": "healthy",
    "backend": "PostgreSQLBackend",
    "uptime_seconds": 86421,
    "principals": 142,
    "objects": 8491,
    "scopes": 37
}
```

### scoped://audit/recent

Returns the most recent audit log entries (default: last 100).

```json
{
    "entries": [
        {
            "id": "aud_x7y8z9",
            "action": "object.create",
            "principal_id": "prin_a1b2c3",
            "timestamp": "2026-03-31T14:22:00Z",
            "details": {"kind": "document", "object_id": "obj_m1n2o3"}
        }
    ]
}
```

## Full Example Server

```python
#!/usr/bin/env python3
"""pyscoped MCP server."""

import argparse
from scoped import ScopedClient
from scoped.contrib.mcp import create_scoped_server


def main():
    parser = argparse.ArgumentParser(description="pyscoped MCP server")
    parser.add_argument(
        "--database-url",
        required=True,
        help="Database connection URL",
    )
    parser.add_argument(
        "--api-key",
        required=True,
        help="pyscoped API key",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="MCP transport to use (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for HTTP transport (default: 8080)",
    )
    args = parser.parse_args()

    client = ScopedClient(
        database_url=args.database_url,
        api_key=args.api_key,
    )

    server = create_scoped_server(client)

    if args.transport == "http":
        server.run(transport="http", port=args.port)
    else:
        server.run()


if __name__ == "__main__":
    main()
```

Run the server:

```bash
# stdio transport (for Claude Desktop and similar clients)
python scoped_server.py \
    --database-url postgresql://localhost/mydb \
    --api-key sk-scoped-production-key

# HTTP transport (for remote clients)
python scoped_server.py \
    --database-url postgresql://localhost/mydb \
    --api-key sk-scoped-production-key \
    --transport http \
    --port 9000
```

## Connecting to Claude Desktop

Add the pyscoped MCP server to your Claude Desktop configuration file.

### macOS

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
    "mcpServers": {
        "pyscoped": {
            "command": "python",
            "args": [
                "/path/to/scoped_server.py",
                "--database-url", "postgresql://localhost/mydb",
                "--api-key", "sk-scoped-production-key"
            ]
        }
    }
}
```

### Windows

Edit `%APPDATA%\Claude\claude_desktop_config.json` with the same structure.

After saving the configuration and restarting Claude Desktop, you will see
the pyscoped tools available in the tools list. Claude can then create
principals, manage objects, query audit logs, and check health status through
natural language requests.

## Connecting to Other MCP Clients

Any MCP-compatible client can connect to the pyscoped server. For stdio-based
clients, spawn the server as a subprocess. For HTTP-based clients, point them
at the server's URL.

```python
# Example: connecting programmatically with the MCP client SDK
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

server_params = StdioServerParameters(
    command="python",
    args=[
        "scoped_server.py",
        "--database-url", "postgresql://localhost/mydb",
        "--api-key", "sk-scoped-key",
    ],
)

async with stdio_client(server_params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()

        # List available tools
        tools = await session.list_tools()
        print([t.name for t in tools.tools])

        # Call a tool
        result = await session.call_tool("health_check", arguments={})
        print(result)

        # Read a resource
        principals = await session.read_resource("scoped://principals")
        print(principals)
```

## Notes

- All tool calls go through the provided `ScopedClient`, so the backend
  configuration, connection pooling, and audit logging apply exactly as they
  would in a direct Python integration.
- The MCP server is stateless between calls. Each tool invocation opens and
  closes its own transaction as managed by the client.
- For production deployments, run the server behind a process manager
  (systemd, supervisord) and ensure the database URL and API key are passed
  via environment variables rather than command-line arguments.
- Resource reads are eventually consistent with tool writes. There is no
  caching layer in the MCP server itself.
