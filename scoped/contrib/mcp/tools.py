"""MCP tool definitions for Scoped operations."""

from __future__ import annotations

from typing import Any


def register_tools(server, client: Any) -> None:
    """Register Scoped operations as MCP tools.

    Args:
        server: A ``FastMCP`` server instance.
        client: A ``ScopedClient`` instance.
    """

    @server.tool()
    def create_principal(kind: str, display_name: str) -> dict[str, str]:
        """Create a new Scoped principal (user, bot, team, etc.)."""
        p = client.principals.create(display_name, kind=kind)
        return {"id": p.id, "kind": p.kind, "display_name": p.display_name}

    @server.tool()
    def create_object(
        object_type: str,
        owner_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a new scoped object (creator-private by default)."""
        obj, ver = client.objects.create(
            object_type, owner_id=owner_id, data=data,
        )
        return {
            "object_id": obj.id,
            "version": ver.version,
            "object_type": obj.object_type,
            "owner_id": obj.owner_id,
        }

    @server.tool()
    def get_object(object_id: str, principal_id: str) -> dict[str, Any] | str:
        """Get an object by ID (owner-only access)."""
        obj = client.objects.get(object_id, principal_id=principal_id)
        if obj is None:
            return "Object not found or access denied"
        return {
            "id": obj.id,
            "object_type": obj.object_type,
            "owner_id": obj.owner_id,
            "current_version": obj.current_version,
            "lifecycle": obj.lifecycle.name,
        }

    @server.tool()
    def create_scope(
        name: str,
        owner_id: str,
        description: str = "",
    ) -> dict[str, Any]:
        """Create a new scope (sharing boundary)."""
        scope = client.scopes.create(name, owner_id=owner_id, description=description)
        return {
            "scope_id": scope.id,
            "name": scope.name,
            "owner_id": scope.owner_id,
        }

    @server.tool()
    def list_audit(
        actor_id: str = "",
        target_id: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Query the audit trail."""
        kwargs: dict[str, Any] = {"limit": limit}
        if actor_id:
            kwargs["actor_id"] = actor_id
        if target_id:
            kwargs["target_id"] = target_id

        entries = client.audit.query(**kwargs)
        return [
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

    @server.tool()
    def health_check() -> dict[str, Any]:
        """Run Scoped framework health checks."""
        from scoped.testing.health import HealthChecker

        checker = HealthChecker(client.backend)
        status = checker.check_all()
        return {
            "healthy": status.healthy,
            "checks": {
                name: {"passed": c.passed, "detail": c.detail}
                for name, c in status.checks.items()
            },
        }
