"""Plugin lifecycle management — install, activate, suspend, uninstall."""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from scoped.exceptions import AccessDeniedError, PluginError
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
from scoped.storage._query import compile_for
from scoped.storage._schema import plugin_hooks, plugin_permissions, plugins
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc
from scoped._stability import stable


@stable(since="1.7.0")
class PluginLifecycleManager:
    """Manage plugin installation, activation, suspension, and removal."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
        rule_engine: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer
        self._rule_engine = rule_engine

    def _check_rule(
        self,
        *,
        action: str,
        principal_id: str,
        object_type: str,
        object_id: str | None = None,
        scope_id: str | None = None,
    ) -> None:
        """Evaluate Layer 5 rules; raise AccessDeniedError if any DENY matched.

        No-op when no rule engine is wired or when no rules are bound.
        Mirrors the pattern used by Layer 9 (flow/pipeline.py).
        """
        if self._rule_engine is None:
            return
        result = self._rule_engine.evaluate(
            action=action,
            principal_id=principal_id,
            object_type=object_type,
            object_id=object_id,
            scope_id=scope_id,
        )
        if not result.allowed and (result.deny_rules or result.matching_rules):
            deny_names = [r.name for r in result.deny_rules]
            raise AccessDeniedError(
                f"{action} denied by rule(s): {deny_names}",
                context={
                    "action": action,
                    "principal_id": principal_id,
                    "object_type": object_type,
                    "object_id": object_id,
                    "scope_id": scope_id,
                    "deny_rules": deny_names,
                },
            )

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
        self._check_rule(
            action="plugin_install",
            principal_id=owner_id,
            object_type="plugin",
            scope_id=scope_id,
        )
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

        stmt = sa.insert(plugins).values(
            id=pid,
            name=name,
            description=description,
            version=version,
            owner_id=owner_id,
            scope_id=scope_id,
            manifest_json=json.dumps(mfst),
            state="installed",
            installed_at=ts.isoformat(),
            metadata_json=json.dumps(meta),
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        try:
            self._backend.execute(sql, params)
        except Exception as exc:
            # Translate UNIQUE-constraint and other integrity violations from
            # the underlying driver into a clean PluginError so callers don't
            # have to import sqlalchemy.exc.IntegrityError to handle a
            # duplicate plugin name.
            msg = str(exc).lower()
            if "unique" in msg or "integrity" in msg or "duplicate" in msg:
                raise PluginError(
                    f"Plugin name '{name}' is already in use",
                    context={"name": name, "owner_id": owner_id},
                ) from exc
            raise

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
        stmt = sa.select(plugins).where(plugins.c.id == plugin_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return plugin_from_row(row) if row else None

    def get_plugin_by_name(self, name: str) -> Plugin | None:
        stmt = sa.select(plugins).where(plugins.c.name == name)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
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
        stmt = sa.select(plugins)
        if owner_id is not None:
            stmt = stmt.where(plugins.c.owner_id == owner_id)
        if state is not None:
            stmt = stmt.where(plugins.c.state == state.value)
        stmt = stmt.order_by(plugins.c.installed_at.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [plugin_from_row(r) for r in rows]

    def _require_owner(self, plugin: Plugin, actor_id: str) -> None:
        """Raise AccessDeniedError if actor is not the plugin owner."""
        if plugin.owner_id != actor_id:
            raise AccessDeniedError(
                f"Principal '{actor_id}' is not the owner of plugin '{plugin.id}'",
                context={
                    "plugin_id": plugin.id,
                    "actor_id": actor_id,
                    "owner_id": plugin.owner_id,
                },
            )

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
        self._require_owner(plugin, actor_id)

        # Layer 5 rule gate: action mirrors the audit action so policy
        # authors can write rules like "deny plugin_activate when ...".
        rule_action_map = {
            ActionType.PLUGIN_ACTIVATE: "plugin_activate",
            ActionType.PLUGIN_SUSPEND: "plugin_suspend",
            ActionType.PLUGIN_UNINSTALL: "plugin_uninstall",
        }
        rule_action = rule_action_map.get(action)
        if rule_action is not None:
            self._check_rule(
                action=rule_action,
                principal_id=actor_id,
                object_type="plugin",
                object_id=plugin_id,
                scope_id=plugin.scope_id,
            )

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
        values: dict[str, Any] = {"state": target_state.value}

        if target_state == PluginState.ACTIVE:
            values["activated_at"] = ts.isoformat()

        stmt = sa.update(plugins).where(plugins.c.id == plugin_id).values(**values)
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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
        stmt = sa.update(plugin_hooks).where(
            (plugin_hooks.c.plugin_id == plugin_id)
            & (plugin_hooks.c.lifecycle == "ACTIVE"),
        ).values(lifecycle="ARCHIVED")
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
        return plugin

    def uninstall(self, plugin_id: str, *, actor_id: str) -> Plugin:
        """Uninstall a plugin — revokes permissions, deactivates hooks."""
        plugin = self._transition(
            plugin_id, PluginState.UNINSTALLED,
            actor_id=actor_id, action=ActionType.PLUGIN_UNINSTALL,
        )
        # Revoke all permissions
        stmt = sa.update(plugin_permissions).where(
            plugin_permissions.c.plugin_id == plugin_id,
        ).values(lifecycle="ARCHIVED")
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        # Deactivate all hooks
        stmt = sa.update(plugin_hooks).where(
            plugin_hooks.c.plugin_id == plugin_id,
        ).values(lifecycle="ARCHIVED")
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
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
        """Grant a permission to a plugin.

        Only the plugin owner may grant permissions; ``granted_by`` is
        treated as the acting principal and must match ``plugin.owner_id``.
        """
        plugin = self.get_plugin_or_raise(plugin_id)
        self._require_owner(plugin, granted_by)
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

        stmt = sa.insert(plugin_permissions).values(
            id=pid,
            plugin_id=plugin_id,
            permission_type=permission_type,
            target_ref=target_ref,
            granted_at=ts.isoformat(),
            granted_by=granted_by,
            lifecycle="ACTIVE",
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit is not None:
            self._audit.record(
                actor_id=granted_by,
                action=ActionType.PLUGIN_PERMISSION_GRANT,
                target_type="plugin_permission",
                target_id=pid,
                after_state=perm.snapshot(),
            )

        return perm

    def revoke_permission(
        self,
        permission_id: str,
        *,
        actor_id: str,
    ) -> None:
        """Revoke a specific permission.

        Only the owning plugin's owner may revoke its permissions.
        """
        stmt = sa.select(plugin_permissions).where(
            plugin_permissions.c.id == permission_id,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            raise PluginError(
                f"Plugin permission {permission_id} not found",
                context={"permission_id": permission_id},
            )
        perm = permission_from_row(row)
        plugin = self.get_plugin_or_raise(perm.plugin_id)
        self._require_owner(plugin, actor_id)

        stmt = sa.update(plugin_permissions).where(
            plugin_permissions.c.id == permission_id,
        ).values(lifecycle="ARCHIVED")
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit is not None:
            self._audit.record(
                actor_id=actor_id,
                action=ActionType.PLUGIN_PERMISSION_REVOKE,
                target_type="plugin_permission",
                target_id=permission_id,
                before_state=perm.snapshot(),
            )

    def get_permissions(
        self,
        plugin_id: str,
        *,
        active_only: bool = True,
    ) -> list[PluginPermission]:
        stmt = sa.select(plugin_permissions).where(
            plugin_permissions.c.plugin_id == plugin_id,
        )
        if active_only:
            stmt = stmt.where(plugin_permissions.c.lifecycle == "ACTIVE")
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [permission_from_row(r) for r in rows]

    def has_permission(
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
