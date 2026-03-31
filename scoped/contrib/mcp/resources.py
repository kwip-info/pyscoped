"""MCP resource definitions for Scoped."""

from __future__ import annotations

import json
from typing import Any


def register_resources(server, services: dict[str, Any]) -> None:
    """Register Scoped data as MCP resources."""

    @server.resource("scoped://principals")
    def list_principals() -> str:
        """List all principals in the system."""
        principals = services["principals"].list_principals()
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
        status = services["health"].check_all()
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
        entries = services["audit_query"].query(limit=50)
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
