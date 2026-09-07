"""MCP resource definitions for Scoped."""

from __future__ import annotations

import json
from typing import Any


def register_resources(server, client: Any) -> None:
    """Register Scoped data as MCP resources.

    Args:
        server: A ``FastMCP`` server instance.
        client: A ``ScopedClient`` instance.
    """

    @server.resource("scoped://principals")
    def list_principals() -> str:
        """List all principals in the system."""
        principals = client.principals.list()
        return json.dumps(
            [
                {
                    "id": p.id,
                    "kind": p.kind,
                    "display_name": p.display_name,
                    "lifecycle": p.lifecycle.name,
                }
                for p in principals
            ]
        )

    @server.resource("scoped://health")
    def health_resource() -> str:
        """Current framework health status."""
        from scoped.testing.health import HealthChecker

        checker = HealthChecker(client.backend)
        status = checker.check_all()
        return json.dumps(
            {
                "healthy": status.healthy,
                "checks": {
                    name: {"passed": c.passed, "detail": c.detail}
                    for name, c in status.checks.items()
                },
            }
        )

    @server.resource("scoped://audit/recent")
    def recent_audit() -> str:
        """Most recent 50 audit entries."""
        entries = client.audit.query(limit=50)
        return json.dumps(
            [
                {
                    "id": e.id,
                    "action": e.action.value,
                    "actor_id": e.actor_id,
                    "target_type": e.target_type,
                    "target_id": e.target_id,
                    "timestamp": e.timestamp.isoformat(),
                }
                for e in entries
            ]
        )
