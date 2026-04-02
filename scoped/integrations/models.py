"""Integration & plugin data models — integrations, plugins, hooks, permissions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from scoped.types import Lifecycle


class PluginState(Enum):
    """Plugin lifecycle states."""

    INSTALLED = "installed"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    UNINSTALLED = "uninstalled"


# Valid transitions: installed→active, active→suspended, suspended→active,
# active→uninstalled, suspended→uninstalled
VALID_PLUGIN_TRANSITIONS: dict[PluginState, frozenset[PluginState]] = {
    PluginState.INSTALLED: frozenset({PluginState.ACTIVE, PluginState.UNINSTALLED}),
    PluginState.ACTIVE: frozenset({PluginState.SUSPENDED, PluginState.UNINSTALLED}),
    PluginState.SUSPENDED: frozenset({PluginState.ACTIVE, PluginState.UNINSTALLED}),
    PluginState.UNINSTALLED: frozenset(),
}


@dataclass(slots=True)
class Integration:
    """A connection to an external system."""

    id: str
    name: str
    integration_type: str
    owner_id: str
    created_at: datetime
    description: str = ""
    scope_id: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    credentials_ref: str | None = None
    lifecycle: Lifecycle = Lifecycle.ACTIVE
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "integration_type": self.integration_type,
            "owner_id": self.owner_id,
            "scope_id": self.scope_id,
            "config": self.config,
            "credentials_ref": self.credentials_ref,
            "lifecycle": self.lifecycle.name,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(slots=True)
class Plugin:
    """A code extension that plugs into framework hooks."""

    id: str
    name: str
    owner_id: str
    installed_at: datetime
    version: str = "0.1.0"
    description: str = ""
    scope_id: str | None = None
    manifest: dict[str, Any] = field(default_factory=dict)
    state: PluginState = PluginState.INSTALLED
    activated_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.state == PluginState.ACTIVE

    @property
    def is_uninstalled(self) -> bool:
        return self.state == PluginState.UNINSTALLED

    @property
    def typed_manifest(self) -> Any:
        """Parse ``manifest`` into a ``PluginManifest`` model.

        Falls back to the raw dict if parsing fails.
        """
        try:
            from scoped.integrations.plugin_types import parse_plugin_manifest
            return parse_plugin_manifest(self.manifest)
        except Exception:
            return self.manifest

    @property
    def typed_metadata(self) -> Any:
        """Parse ``metadata`` into a ``PluginMetadata`` model.

        Falls back to the raw dict if parsing fails.
        """
        try:
            from scoped.integrations.plugin_types import parse_plugin_metadata
            return parse_plugin_metadata(self.metadata)
        except Exception:
            return self.metadata

    def can_transition_to(self, target: PluginState) -> bool:
        return target in VALID_PLUGIN_TRANSITIONS.get(self.state, frozenset())

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "owner_id": self.owner_id,
            "scope_id": self.scope_id,
            "state": self.state.value,
            "manifest": self.manifest,
            "metadata": self.metadata,
            "installed_at": self.installed_at.isoformat(),
            "activated_at": self.activated_at.isoformat() if self.activated_at else None,
        }


@dataclass(frozen=True, slots=True)
class PluginHook:
    """A registered hook binding for a plugin."""

    id: str
    plugin_id: str
    hook_point: str
    handler_ref: str
    priority: int = 0
    lifecycle: Lifecycle = Lifecycle.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "plugin_id": self.plugin_id,
            "hook_point": self.hook_point,
            "handler_ref": self.handler_ref,
            "priority": self.priority,
            "lifecycle": self.lifecycle.name,
        }


@dataclass(frozen=True, slots=True)
class PluginPermission:
    """A granted permission for a plugin."""

    id: str
    plugin_id: str
    permission_type: str
    target_ref: str
    granted_at: datetime
    granted_by: str
    lifecycle: Lifecycle = Lifecycle.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "plugin_id": self.plugin_id,
            "permission_type": self.permission_type,
            "target_ref": self.target_ref,
            "granted_at": self.granted_at.isoformat(),
            "granted_by": self.granted_by,
            "lifecycle": self.lifecycle.name,
        }


# -- Row mapping helpers ---------------------------------------------------

def integration_from_row(row: dict[str, Any]) -> Integration:
    config = row.get("config_json", "{}")
    if isinstance(config, str):
        config = json.loads(config)
    meta = row.get("metadata_json", "{}")
    if isinstance(meta, str):
        meta = json.loads(meta)
    return Integration(
        id=row["id"],
        name=row["name"],
        description=row.get("description", ""),
        integration_type=row["integration_type"],
        owner_id=row["owner_id"],
        scope_id=row.get("scope_id"),
        config=config,
        credentials_ref=row.get("credentials_ref"),
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
        metadata=meta,
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def plugin_from_row(row: dict[str, Any]) -> Plugin:
    manifest = row.get("manifest_json", "{}")
    if isinstance(manifest, str):
        manifest = json.loads(manifest)
    meta = row.get("metadata_json", "{}")
    if isinstance(meta, str):
        meta = json.loads(meta)
    activated = row.get("activated_at")
    return Plugin(
        id=row["id"],
        name=row["name"],
        description=row.get("description", ""),
        version=row.get("version", "0.1.0"),
        owner_id=row["owner_id"],
        scope_id=row.get("scope_id"),
        manifest=manifest,
        state=PluginState(row.get("state", "installed")),
        installed_at=datetime.fromisoformat(row["installed_at"]),
        activated_at=datetime.fromisoformat(activated) if activated else None,
        metadata=meta,
    )


def hook_from_row(row: dict[str, Any]) -> PluginHook:
    return PluginHook(
        id=row["id"],
        plugin_id=row["plugin_id"],
        hook_point=row["hook_point"],
        handler_ref=row["handler_ref"],
        priority=row.get("priority", 0),
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
    )


def permission_from_row(row: dict[str, Any]) -> PluginPermission:
    return PluginPermission(
        id=row["id"],
        plugin_id=row["plugin_id"],
        permission_type=row["permission_type"],
        target_ref=row["target_ref"],
        granted_at=datetime.fromisoformat(row["granted_at"]),
        granted_by=row["granted_by"],
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
    )
