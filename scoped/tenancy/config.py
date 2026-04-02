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

import sqlalchemy as sa

from scoped.exceptions import (
    AccessDeniedError,
    ScopeFrozenError,
    ScopeNotFoundError,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import scope_settings, scopes
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
    """Result of resolving a setting through the scope hierarchy.

    ``resolution_chain`` shows all ancestor values encountered during
    hierarchy traversal, ordered from root to leaf.  Each entry is a
    ``(scope_id, value)`` tuple.  The winning value is always the
    **last** entry (closest to the queried scope).
    """

    key: str
    value: Any
    source_scope_id: str
    inherited: bool  # True if resolved from an ancestor, not the queried scope
    resolution_chain: list[tuple[str, Any]] = field(default_factory=list)


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
        stmt = sa.select(scope_settings).where(
            sa.and_(
                scope_settings.c.scope_id == scope_id,
                scope_settings.c.key == key,
                scope_settings.c.lifecycle == Lifecycle.ACTIVE.name,
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        existing = self._backend.fetch_one(sql, params)

        if existing is not None:
            # Update existing
            before = setting_from_row(existing)
            update_stmt = sa.update(scope_settings).where(
                scope_settings.c.id == existing["id"],
            ).values(
                value_json=value_json,
                updated_at=ts.isoformat(),
                updated_by=principal_id,
                description=description,
            )
            sql, params = compile_for(update_stmt, self._backend.dialect)
            self._backend.execute(sql, params)
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
        insert_stmt = sa.insert(scope_settings).values(
            id=setting_id,
            scope_id=scope_id,
            key=key,
            value_json=value_json,
            created_at=ts.isoformat(),
            created_by=principal_id,
            description=description,
            lifecycle=Lifecycle.ACTIVE.name,
        )
        sql, params = compile_for(insert_stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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
        stmt = sa.select(scope_settings).where(
            sa.and_(
                scope_settings.c.scope_id == scope_id,
                scope_settings.c.key == key,
                scope_settings.c.lifecycle == Lifecycle.ACTIVE.name,
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
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

        select_stmt = sa.select(scope_settings).where(
            sa.and_(
                scope_settings.c.scope_id == scope_id,
                scope_settings.c.key == key,
                scope_settings.c.lifecycle == Lifecycle.ACTIVE.name,
            )
        )
        sql, params = compile_for(select_stmt, self._backend.dialect)
        existing = self._backend.fetch_one(sql, params)
        if existing is None:
            return False

        update_stmt = sa.update(scope_settings).where(
            scope_settings.c.id == existing["id"],
        ).values(lifecycle=Lifecycle.ARCHIVED.name)
        sql, params = compile_for(update_stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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
        stmt = sa.select(scope_settings).where(
            scope_settings.c.scope_id == scope_id,
        )
        if not include_archived:
            stmt = stmt.where(scope_settings.c.lifecycle == Lifecycle.ACTIVE.name)
        stmt = stmt.order_by(scope_settings.c.key.asc())

        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
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
        The ``resolution_chain`` shows every ancestor that has a value
        for this key, ordered root-to-leaf.
        """
        # First pass: collect the scope chain (target → root)
        chain_ids: list[str] = []
        current = scope_id
        for _ in range(self._max_depth + 1):
            chain_ids.append(current)
            row = self._backend.fetch_one(
                "SELECT parent_scope_id FROM scopes WHERE id = ?",
                (current,),
            )
            if row is None or row["parent_scope_id"] is None:
                break
            current = row["parent_scope_id"]

        # Second pass: collect all values root-to-leaf
        resolution_chain: list[tuple[str, Any]] = []
        for sid in reversed(chain_ids):
            setting = self._store.get(sid, key)
            if setting is not None:
                resolution_chain.append((sid, setting.value))

        if not resolution_chain:
            return None

        # Winner is the closest to the queried scope (last in chain)
        winner_scope, winner_value = resolution_chain[-1]
        return ResolvedSetting(
            key=key,
            value=winner_value,
            source_scope_id=winner_scope,
            inherited=(winner_scope != scope_id),
            resolution_chain=resolution_chain,
        )

    def resolve_all(
        self,
        scope_id: str,
    ) -> dict[str, ResolvedSetting]:
        """Resolve all settings visible to a scope (own + inherited).

        Child overrides win over parent values.  Each ``ResolvedSetting``
        includes a ``resolution_chain`` showing all ancestor values for
        that key, ordered root-to-leaf.
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

        # Walk from root to leaf, collecting all values per key
        chains: dict[str, list[tuple[str, Any]]] = {}
        result: dict[str, ResolvedSetting] = {}

        for sid in reversed(chain):
            settings = self._store.list_settings(sid)
            for s in settings:
                if s.key not in chains:
                    chains[s.key] = []
                chains[s.key].append((sid, s.value))
                result[s.key] = ResolvedSetting(
                    key=s.key,
                    value=s.value,
                    source_scope_id=sid,
                    inherited=(sid != scope_id),
                    resolution_chain=list(chains[s.key]),
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
