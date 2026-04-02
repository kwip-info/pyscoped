"""Plugin sandbox — permission enforcement and isolation checks."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from scoped.exceptions import PluginPermissionError, PluginSandboxError
from scoped.integrations.models import PluginState, plugin_from_row
from scoped.storage._query import compile_for
from scoped.storage._schema import plugin_permissions, plugins
from scoped.storage.interface import StorageBackend
from scoped._stability import experimental


@experimental()
class PluginSandbox:
    """Enforce plugin isolation and permission boundaries.

    The sandbox ensures plugins can only:
    - Access objects within their granted scopes
    - Read secrets they've been granted refs for
    - Modify the registry only for their declared kinds
    - Never bypass the audit trail
    - Never modify other plugins' data
    """

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def check_permission(
        self,
        plugin_id: str,
        permission_type: str,
        target_ref: str,
    ) -> bool:
        """Check if a plugin has a specific active permission."""
        stmt = sa.select(plugin_permissions.c.id).where(
            (plugin_permissions.c.plugin_id == plugin_id)
            & (plugin_permissions.c.permission_type == permission_type)
            & (plugin_permissions.c.target_ref == target_ref)
            & (plugin_permissions.c.lifecycle == "ACTIVE"),
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return row is not None

    def require_permission(
        self,
        plugin_id: str,
        permission_type: str,
        target_ref: str,
    ) -> None:
        """Require a permission — raises PluginPermissionError if not granted."""
        if not self.check_permission(plugin_id, permission_type, target_ref):
            raise PluginPermissionError(
                f"Plugin {plugin_id} lacks permission {permission_type} on {target_ref}",
                context={
                    "plugin_id": plugin_id,
                    "permission_type": permission_type,
                    "target_ref": target_ref,
                },
            )

    def require_active(self, plugin_id: str) -> None:
        """Require that a plugin is active — raises PluginSandboxError if not."""
        stmt = sa.select(plugins).where(plugins.c.id == plugin_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            raise PluginSandboxError(
                f"Plugin {plugin_id} not found",
                context={"plugin_id": plugin_id},
            )
        plugin = plugin_from_row(row)
        if plugin.state != PluginState.ACTIVE:
            raise PluginSandboxError(
                f"Plugin {plugin_id} is not active (state: {plugin.state.value})",
                context={"plugin_id": plugin_id, "state": plugin.state.value},
            )

    def check_scope_access(
        self,
        plugin_id: str,
        scope_id: str,
    ) -> bool:
        """Check if a plugin has scope_access permission for a given scope."""
        return self.check_permission(plugin_id, "scope_access", scope_id)

    def require_scope_access(
        self,
        plugin_id: str,
        scope_id: str,
    ) -> None:
        """Require scope_access — raises PluginPermissionError if denied."""
        self.require_permission(plugin_id, "scope_access", scope_id)

    def check_object_type_access(
        self,
        plugin_id: str,
        object_type: str,
    ) -> bool:
        """Check if a plugin has object_type permission for a given type."""
        return self.check_permission(plugin_id, "object_type", object_type)

    def check_secret_access(
        self,
        plugin_id: str,
        secret_ref: str,
    ) -> bool:
        """Check if a plugin has secret_access permission for a given ref."""
        return self.check_permission(plugin_id, "secret_access", secret_ref)

    def require_secret_access(
        self,
        plugin_id: str,
        secret_ref: str,
    ) -> None:
        """Require secret_access — raises PluginPermissionError if denied."""
        self.require_permission(plugin_id, "secret_access", secret_ref)

    def check_hook_access(
        self,
        plugin_id: str,
        hook_point: str,
    ) -> bool:
        """Check if a plugin has hook permission for a given hook point."""
        return self.check_permission(plugin_id, "hook", hook_point)

    def get_allowed_scopes(self, plugin_id: str) -> list[str]:
        """Get all scope IDs a plugin has access to."""
        stmt = sa.select(plugin_permissions.c.target_ref).where(
            (plugin_permissions.c.plugin_id == plugin_id)
            & (plugin_permissions.c.permission_type == "scope_access")
            & (plugin_permissions.c.lifecycle == "ACTIVE"),
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [r["target_ref"] for r in rows]

    def get_allowed_object_types(self, plugin_id: str) -> list[str]:
        """Get all object types a plugin can access."""
        stmt = sa.select(plugin_permissions.c.target_ref).where(
            (plugin_permissions.c.plugin_id == plugin_id)
            & (plugin_permissions.c.permission_type == "object_type")
            & (plugin_permissions.c.lifecycle == "ACTIVE"),
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [r["target_ref"] for r in rows]
