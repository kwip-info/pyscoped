"""Plugin lifecycle management — install, activate, suspend, uninstall."""

from __future__ import annotations

import json
from typing import Any

from scoped.exceptions import PluginError
from scoped.registry.base import get_registry
from scoped.registry.kinds import RegistryKind
from scoped.registry.sqlite_store import SQLiteRegistryStore
from scoped.integrations.models import (
    Plugin,
    PluginPermission,
    PluginState,
    VALID_PLUGIN_TRANSITIONS,
    permission_from_row,
    plugin_from_row,
)
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc


class PluginLifecycleManager:
    """Manage plugin installation, activation, suspension, and removal."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer

    # -- Install -----------------------------------------------------------

    def install_plugin(
        self,
        *,
        name: str,
        owner_id: str,
        version: str = "0.1.0",
        description: str = "",
        scope_id: str | None = None,
        manifest: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Plugin:
        """Install a new plugin (state=installed, not yet active)."""
        ts = now_utc()
        pid = generate_id()
        mfst = manifest or {}
        meta = metadata or {}

        plugin = Plugin(
            id=pid,
            name=name,
            description=description,
            version=version,
            owner_id=owner_id,
            scope_id=scope_id,
            manifest=mfst,
            state=PluginState.INSTALLED,
            installed_at=ts,
            metadata=meta,
        )

        self._backend.execute(
            """INSERT INTO plugins
               (id, name, description, version, owner_id, scope_id,
                manifest_json, state, installed_at, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pid, name, description, version, owner_id, scope_id,
             json.dumps(mfst), "installed", ts.isoformat(), json.dumps(meta)),
        )

        # Auto-register in registry (Invariant #1)
        try:
            reg = get_registry()
            entry = reg.register(
                kind=RegistryKind.PLUGIN,
                namespace="integrations",
                name=f"plugin:{pid}",
                registered_by=owner_id,
                metadata={"plugin_name": name, "version": version},
            )
            SQLiteRegistryStore(self._backend).save_entry(entry)
        except Exception:
            pass

        if self._audit is not None:
            self._audit.record(
                actor_id=owner_id,
                action=ActionType.PLUGIN_INSTALL,
                target_type="plugin",
                target_id=pid,
                after_state=plugin.snapshot(),
            )

        return plugin

    # -- State transitions -------------------------------------------------

    def get_plugin(self, plugin_id: str) -> Plugin | None:
        row = self._backend.fetch_one(
            "SELECT * FROM plugins WHERE id = ?", (plugin_id,),
        )
        return plugin_from_row(row) if row else None

    def get_plugin_by_name(self, name: str) -> Plugin | None:
        row = self._backend.fetch_one(
            "SELECT * FROM plugins WHERE name = ?", (name,),
        )
        return plugin_from_row(row) if row else None

    def get_plugin_or_raise(self, plugin_id: str) -> Plugin:
        p = self.get_plugin(plugin_id)
        if p is None:
            raise PluginError(
                f"Plugin {plugin_id} not found",
                context={"plugin_id": plugin_id},
            )
        return p

    def list_plugins(
        self,
        *,
        owner_id: str | None = None,
        state: PluginState | None = None,
        limit: int = 100,
    ) -> list[Plugin]:
        clauses: list[str] = []
        params: list[Any] = []
        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        if state is not None:
            clauses.append("state = ?")
            params.append(state.value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = self._backend.fetch_all(
            f"SELECT * FROM plugins{where} ORDER BY installed_at DESC LIMIT ?",
            tuple(params),
        )
        return [plugin_from_row(r) for r in rows]

    def _transition(
        self,
        plugin_id: str,
        target_state: PluginState,
        *,
        actor_id: str,
        action: ActionType,
    ) -> Plugin:
        """Transition a plugin to a new state with validation."""
        plugin = self.get_plugin_or_raise(plugin_id)

        if not plugin.can_transition_to(target_state):
            raise PluginError(
                f"Cannot transition plugin from {plugin.state.value} to {target_state.value}",
                context={
                    "plugin_id": plugin_id,
                    "current_state": plugin.state.value,
                    "target_state": target_state.value,
                },
            )

        ts = now_utc()
        updates = ["state = ?"]
        params: list[Any] = [target_state.value]

        if target_state == PluginState.ACTIVE:
            updates.append("activated_at = ?")
            params.append(ts.isoformat())

        params.append(plugin_id)
        self._backend.execute(
            f"UPDATE plugins SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )

        plugin.state = target_state
        if target_state == PluginState.ACTIVE:
            plugin.activated_at = ts

        if self._audit is not None:
            self._audit.record(
                actor_id=actor_id,
                action=action,
                target_type="plugin",
                target_id=plugin_id,
                after_state=plugin.snapshot(),
            )

        return plugin

    def activate(self, plugin_id: str, *, actor_id: str) -> Plugin:
        """Activate an installed or suspended plugin."""
        return self._transition(
            plugin_id, PluginState.ACTIVE,
            actor_id=actor_id, action=ActionType.PLUGIN_ACTIVATE,
        )

    def suspend(self, plugin_id: str, *, actor_id: str) -> Plugin:
        """Suspend an active plugin."""
        plugin = self._transition(
            plugin_id, PluginState.SUSPENDED,
            actor_id=actor_id, action=ActionType.PLUGIN_SUSPEND,
        )
        # Deactivate all hooks when suspended
        self._backend.execute(
            "UPDATE plugin_hooks SET lifecycle = 'ARCHIVED' WHERE plugin_id = ? AND lifecycle = 'ACTIVE'",
            (plugin_id,),
        )
        return plugin

    def uninstall(self, plugin_id: str, *, actor_id: str) -> Plugin:
        """Uninstall a plugin — revokes permissions, deactivates hooks."""
        plugin = self._transition(
            plugin_id, PluginState.UNINSTALLED,
            actor_id=actor_id, action=ActionType.PLUGIN_UNINSTALL,
        )
        # Revoke all permissions
        self._backend.execute(
            "UPDATE plugin_permissions SET lifecycle = 'ARCHIVED' WHERE plugin_id = ?",
            (plugin_id,),
        )
        # Deactivate all hooks
        self._backend.execute(
            "UPDATE plugin_hooks SET lifecycle = 'ARCHIVED' WHERE plugin_id = ?",
            (plugin_id,),
        )
        return plugin

    # -- Permissions -------------------------------------------------------

    def grant_permission(
        self,
        *,
        plugin_id: str,
        permission_type: str,
        target_ref: str,
        granted_by: str,
    ) -> PluginPermission:
        """Grant a permission to a plugin."""
        self.get_plugin_or_raise(plugin_id)
        ts = now_utc()
        pid = generate_id()

        perm = PluginPermission(
            id=pid,
            plugin_id=plugin_id,
            permission_type=permission_type,
            target_ref=target_ref,
            granted_at=ts,
            granted_by=granted_by,
        )

        self._backend.execute(
            """INSERT INTO plugin_permissions
               (id, plugin_id, permission_type, target_ref, granted_at, granted_by, lifecycle)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (pid, plugin_id, permission_type, target_ref,
             ts.isoformat(), granted_by, "ACTIVE"),
        )

        return perm

    def revoke_permission(self, permission_id: str) -> None:
        """Revoke a specific permission."""
        self._backend.execute(
            "UPDATE plugin_permissions SET lifecycle = 'ARCHIVED' WHERE id = ?",
            (permission_id,),
        )

    def get_permissions(
        self,
        plugin_id: str,
        *,
        active_only: bool = True,
    ) -> list[PluginPermission]:
        if active_only:
            rows = self._backend.fetch_all(
                "SELECT * FROM plugin_permissions WHERE plugin_id = ? AND lifecycle = 'ACTIVE'",
                (plugin_id,),
            )
        else:
            rows = self._backend.fetch_all(
                "SELECT * FROM plugin_permissions WHERE plugin_id = ?",
                (plugin_id,),
            )
        return [permission_from_row(r) for r in rows]

    def has_permission(
        self,
        plugin_id: str,
        permission_type: str,
        target_ref: str,
    ) -> bool:
        """Check if a plugin has a specific active permission."""
        row = self._backend.fetch_one(
            """SELECT id FROM plugin_permissions
               WHERE plugin_id = ? AND permission_type = ? AND target_ref = ?
               AND lifecycle = 'ACTIVE'""",
            (plugin_id, permission_type, target_ref),
        )
        return row is not None
