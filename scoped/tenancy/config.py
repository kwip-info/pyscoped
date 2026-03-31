"""Configuration hierarchy — per-scope settings with inheritance.

Settings are key-value pairs bound to scopes. Child scopes inherit
parent settings unless overridden. Resolution walks up the scope
hierarchy; first match wins.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from scoped.exceptions import (
    AccessDeniedError,
    ScopeFrozenError,
    ScopeNotFoundError,
)
from scoped.storage.interface import StorageBackend
from scoped.tenancy.models import _lifecycle_to_db, scope_from_row
from scoped.types import ActionType, Lifecycle, generate_id, now_utc


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ScopedSetting:
    """A key-value configuration entry bound to a scope."""

    id: str
    scope_id: str
    key: str
    value: Any  # deserialized from JSON
    created_at: datetime
    created_by: str
    updated_at: datetime | None = None
    updated_by: str | None = None
    description: str = ""
    lifecycle: Lifecycle = Lifecycle.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope_id": self.scope_id,
            "key": self.key,
            "value": self.value,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "updated_by": self.updated_by,
            "description": self.description,
            "lifecycle": self.lifecycle.name,
        }


@dataclass(frozen=True, slots=True)
class ResolvedSetting:
    """Result of resolving a setting through the scope hierarchy."""

    key: str
    value: Any
    source_scope_id: str
    inherited: bool  # True if resolved from an ancestor, not the queried scope


def setting_from_row(row: dict[str, Any]) -> ScopedSetting:
    """Convert a database row to a ScopedSetting."""
    value_raw = row.get("value_json", "null")
    updated_at_raw = row.get("updated_at")
    return ScopedSetting(
        id=row["id"],
        scope_id=row["scope_id"],
        key=row["key"],
        value=json.loads(value_raw) if value_raw else None,
        created_at=datetime.fromisoformat(row["created_at"]),
        created_by=row["created_by"],
        updated_at=datetime.fromisoformat(updated_at_raw) if updated_at_raw else None,
        updated_by=row.get("updated_by"),
        description=row.get("description", ""),
        lifecycle=Lifecycle[row["lifecycle"]],
    )


# ---------------------------------------------------------------------------
# ConfigStore — CRUD for scope settings
# ---------------------------------------------------------------------------

class ConfigStore:
    """Manages per-scope configuration settings.

    Settings are key-value pairs. Only scope owners and admins can write.
    Members can read. Values are JSON-serializable.
    """

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer

    def set(
        self,
        scope_id: str,
        *,
        key: str,
        value: Any,
        principal_id: str,
        description: str = "",
    ) -> ScopedSetting:
        """Set a configuration value. Creates or updates.

        Only the scope owner can write settings.
        Raises ScopeNotFoundError if scope doesn't exist.
        Raises AccessDeniedError if principal is not the scope owner.
        Raises ScopeFrozenError if scope is frozen/archived.
        """
        scope = self._get_scope_or_raise(scope_id)
        self._require_mutable(scope)
        self._require_owner(scope, principal_id)

        ts = now_utc()
        value_json = json.dumps(value)

        # Check if setting already exists
        existing = self._backend.fetch_one(
            "SELECT * FROM scope_settings "
            "WHERE scope_id = ? AND key = ? AND lifecycle = ?",
            (scope_id, key, Lifecycle.ACTIVE.name),
        )

        if existing is not None:
            # Update existing
            before = setting_from_row(existing)
            self._backend.execute(
                "UPDATE scope_settings SET value_json = ?, updated_at = ?, "
                "updated_by = ?, description = ? "
                "WHERE id = ?",
                (value_json, ts.isoformat(), principal_id, description, existing["id"]),
            )
            setting = ScopedSetting(
                id=existing["id"],
                scope_id=scope_id,
                key=key,
                value=value,
                created_at=before.created_at,
                created_by=before.created_by,
                updated_at=ts,
                updated_by=principal_id,
                description=description,
                lifecycle=Lifecycle.ACTIVE,
            )
            if self._audit:
                self._audit.record(
                    actor_id=principal_id,
                    action=ActionType.CONFIG_SET,
                    target_type="ScopedSetting",
                    target_id=setting.id,
                    scope_id=scope_id,
                    before_state=before.snapshot(),
                    after_state=setting.snapshot(),
                )
            return setting

        # Create new
        setting_id = generate_id()
        setting = ScopedSetting(
            id=setting_id,
            scope_id=scope_id,
            key=key,
            value=value,
            created_at=ts,
            created_by=principal_id,
            description=description,
        )
        self._backend.execute(
            "INSERT INTO scope_settings "
            "(id, scope_id, key, value_json, created_at, created_by, description, lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                setting_id, scope_id, key, value_json,
                ts.isoformat(), principal_id, description,
                Lifecycle.ACTIVE.name,
            ),
        )

        if self._audit:
            self._audit.record(
                actor_id=principal_id,
                action=ActionType.CONFIG_SET,
                target_type="ScopedSetting",
                target_id=setting_id,
                scope_id=scope_id,
                after_state=setting.snapshot(),
            )

        return setting

    def get(
        self,
        scope_id: str,
        key: str,
    ) -> ScopedSetting | None:
        """Get a setting directly from a scope (no inheritance)."""
        row = self._backend.fetch_one(
            "SELECT * FROM scope_settings "
            "WHERE scope_id = ? AND key = ? AND lifecycle = ?",
            (scope_id, key, Lifecycle.ACTIVE.name),
        )
        if row is None:
            return None
        return setting_from_row(row)

    def delete(
        self,
        scope_id: str,
        *,
        key: str,
        principal_id: str,
    ) -> bool:
        """Delete (archive) a setting. Returns True if it existed.

        Raises AccessDeniedError if principal is not the scope owner.
        """
        scope = self._get_scope_or_raise(scope_id)
        self._require_mutable(scope)
        self._require_owner(scope, principal_id)

        existing = self._backend.fetch_one(
            "SELECT * FROM scope_settings "
            "WHERE scope_id = ? AND key = ? AND lifecycle = ?",
            (scope_id, key, Lifecycle.ACTIVE.name),
        )
        if existing is None:
            return False

        self._backend.execute(
            "UPDATE scope_settings SET lifecycle = ? WHERE id = ?",
            (Lifecycle.ARCHIVED.name, existing["id"]),
        )

        if self._audit:
            self._audit.record(
                actor_id=principal_id,
                action=ActionType.CONFIG_DELETE,
                target_type="ScopedSetting",
                target_id=existing["id"],
                scope_id=scope_id,
                before_state=setting_from_row(existing).snapshot(),
            )

        return True

    def list_settings(
        self,
        scope_id: str,
        *,
        include_archived: bool = False,
    ) -> list[ScopedSetting]:
        """List all settings for a scope (no inheritance)."""
        clauses = ["scope_id = ?"]
        params: list[Any] = [scope_id]
        if not include_archived:
            clauses.append("lifecycle = ?")
            params.append(Lifecycle.ACTIVE.name)

        where = " AND ".join(clauses)
        rows = self._backend.fetch_all(
            f"SELECT * FROM scope_settings WHERE {where} ORDER BY key ASC",
            tuple(params),
        )
        return [setting_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_scope_or_raise(self, scope_id: str) -> Any:
        """Fetch scope row, raise ScopeNotFoundError if missing."""
        row = self._backend.fetch_one(
            "SELECT * FROM scopes WHERE id = ?", (scope_id,),
        )
        if row is None:
            raise ScopeNotFoundError(
                f"Scope {scope_id} not found",
                context={"scope_id": scope_id},
            )
        return scope_from_row(row)

    @staticmethod
    def _require_mutable(scope: Any) -> None:
        if scope.is_frozen:
            raise ScopeFrozenError(
                f"Scope {scope.id} is frozen",
                context={"scope_id": scope.id},
            )
        if scope.is_archived:
            raise ScopeFrozenError(
                f"Scope {scope.id} is archived",
                context={"scope_id": scope.id},
            )

    @staticmethod
    def _require_owner(scope: Any, principal_id: str) -> None:
        if scope.owner_id != principal_id:
            raise AccessDeniedError(
                f"Principal {principal_id} is not the owner of scope {scope.id}",
                context={"scope_id": scope.id, "principal_id": principal_id},
            )


# ---------------------------------------------------------------------------
# ConfigResolver — inheritance-aware resolution
# ---------------------------------------------------------------------------

class ConfigResolver:
    """Resolves settings with scope hierarchy inheritance.

    Resolution order: check the target scope first, then walk up the
    parent chain. First match wins. Max depth prevents infinite loops.
    """

    def __init__(
        self,
        backend: StorageBackend,
        *,
        max_depth: int = 20,
    ) -> None:
        self._backend = backend
        self._store = ConfigStore(backend)
        self._max_depth = max_depth

    def resolve(
        self,
        scope_id: str,
        key: str,
    ) -> ResolvedSetting | None:
        """Resolve a setting, walking up the scope hierarchy.

        Returns None if the key is not set anywhere in the hierarchy.
        """
        current = scope_id
        for depth in range(self._max_depth + 1):
            setting = self._store.get(current, key)
            if setting is not None:
                return ResolvedSetting(
                    key=key,
                    value=setting.value,
                    source_scope_id=current,
                    inherited=(current != scope_id),
                )

            # Walk up
            row = self._backend.fetch_one(
                "SELECT parent_scope_id FROM scopes WHERE id = ?",
                (current,),
            )
            if row is None or row["parent_scope_id"] is None:
                break
            current = row["parent_scope_id"]

        return None

    def resolve_all(
        self,
        scope_id: str,
    ) -> dict[str, ResolvedSetting]:
        """Resolve all settings visible to a scope (own + inherited).

        Child overrides win over parent values.
        """
        # Collect scope chain from target up to root
        chain: list[str] = []
        current = scope_id
        for _ in range(self._max_depth + 1):
            chain.append(current)
            row = self._backend.fetch_one(
                "SELECT parent_scope_id FROM scopes WHERE id = ?",
                (current,),
            )
            if row is None or row["parent_scope_id"] is None:
                break
            current = row["parent_scope_id"]

        # Walk from root to leaf so child overrides parent
        result: dict[str, ResolvedSetting] = {}
        for sid in reversed(chain):
            settings = self._store.list_settings(sid)
            for s in settings:
                result[s.key] = ResolvedSetting(
                    key=s.key,
                    value=s.value,
                    source_scope_id=sid,
                    inherited=(sid != scope_id),
                )

        return result

    def effective_value(
        self,
        scope_id: str,
        key: str,
        *,
        default: Any = None,
    ) -> Any:
        """Convenience: resolve a key and return just the value, or default."""
        resolved = self.resolve(scope_id, key)
        if resolved is None:
            return default
        return resolved.value
