"""Plugins namespace — Layer 12 plugin lifecycle.

Usage::

    import scoped

    with scoped.as_principal(alice):
        plugin = scoped.plugins.install("my-extension")
        scoped.plugins.activate(plugin)

        scoped.plugins.grant_permission(
            plugin, permission_type="scope_access", target_ref=team.id,
        )

        scoped.plugins.suspend(plugin)
        scoped.plugins.uninstall(plugin)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from scoped._namespaces._base import _resolve_principal_id, _to_id

if TYPE_CHECKING:
    from scoped.integrations.models import (
        Plugin,
        PluginPermission,
        PluginState,
    )


class PluginsNamespace:
    """Simplified API for Layer 12 plugin lifecycle.

    Wraps ``PluginLifecycleManager`` with context-aware defaults. The
    acting principal is inferred from ``ScopedContext`` when not passed
    explicitly.
    """

    def __init__(self, services: Any) -> None:
        self._svc = services

    # -- Install / state transitions ---------------------------------------

    def install(
        self,
        name: str,
        *,
        owner_id: str | None = None,
        version: str = "0.1.0",
        description: str = "",
        scope_id: str | Any | None = None,
        manifest: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Plugin:
        """Install a new plugin (state=installed, not yet active)."""
        return self._svc.plugins.install_plugin(
            name=name,
            owner_id=_resolve_principal_id(owner_id),
            version=version,
            description=description,
            scope_id=_to_id(scope_id) if scope_id is not None else None,
            manifest=manifest,
            metadata=metadata,
        )

    def activate(self, plugin: str | Any, *, actor_id: str | None = None) -> Plugin:
        """Transition INSTALLED|SUSPENDED -> ACTIVE."""
        return self._svc.plugins.activate(
            _to_id(plugin), actor_id=_resolve_principal_id(actor_id),
        )

    def suspend(self, plugin: str | Any, *, actor_id: str | None = None) -> Plugin:
        """Transition ACTIVE -> SUSPENDED. Deactivates all hooks."""
        return self._svc.plugins.suspend(
            _to_id(plugin), actor_id=_resolve_principal_id(actor_id),
        )

    def uninstall(self, plugin: str | Any, *, actor_id: str | None = None) -> Plugin:
        """Transition -> UNINSTALLED. Revokes all permissions, deactivates all hooks."""
        return self._svc.plugins.uninstall(
            _to_id(plugin), actor_id=_resolve_principal_id(actor_id),
        )

    # -- Queries -----------------------------------------------------------

    def get(self, plugin_id: str) -> Plugin | None:
        """Fetch a plugin by ID."""
        return self._svc.plugins.get_plugin(plugin_id)

    def get_by_name(self, name: str) -> Plugin | None:
        """Fetch a plugin by its globally unique name."""
        return self._svc.plugins.get_plugin_by_name(name)

    def list(
        self,
        *,
        owner_id: str | None = None,
        state: PluginState | None = None,
        limit: int = 100,
    ) -> list[Plugin]:
        """List plugins with optional filters."""
        return self._svc.plugins.list_plugins(
            owner_id=owner_id, state=state, limit=limit,
        )

    # -- Permissions -------------------------------------------------------

    def grant_permission(
        self,
        plugin: str | Any,
        *,
        permission_type: str,
        target_ref: str | Any,
        granted_by: str | None = None,
    ) -> PluginPermission:
        """Grant a permission to a plugin (only the plugin owner may call)."""
        return self._svc.plugins.grant_permission(
            plugin_id=_to_id(plugin),
            permission_type=permission_type,
            target_ref=_to_id(target_ref) if not isinstance(target_ref, str) else target_ref,
            granted_by=_resolve_principal_id(granted_by),
        )

    def revoke_permission(
        self,
        permission_id: str,
        *,
        actor_id: str | None = None,
    ) -> None:
        """Revoke a specific permission (only the plugin owner may call)."""
        self._svc.plugins.revoke_permission(
            permission_id, actor_id=_resolve_principal_id(actor_id),
        )

    def permissions(
        self,
        plugin: str | Any,
        *,
        active_only: bool = True,
    ) -> list[PluginPermission]:
        """List permissions granted to a plugin."""
        return self._svc.plugins.get_permissions(
            _to_id(plugin), active_only=active_only,
        )

    def has_permission(
        self,
        plugin: str | Any,
        permission_type: str,
        target_ref: str,
    ) -> bool:
        """Check if a plugin has a specific active permission."""
        return self._svc.plugins.has_permission(
            _to_id(plugin), permission_type, target_ref,
        )
