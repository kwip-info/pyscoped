"""Scope lifecycle management — create, freeze, archive, dissolve."""

from __future__ import annotations

import json
from typing import Any

from scoped.exceptions import (
    AccessDeniedError,
    ScopeFrozenError,
    ScopeNotFoundError,
)
from scoped.storage.interface import StorageBackend
from scoped.tenancy.models import (
    SCOPE_LIFECYCLE_FROZEN,
    Scope,
    ScopeMembership,
    ScopeRole,
    _lifecycle_to_db,
    membership_from_row,
    scope_from_row,
)
from scoped.types import ActionType, Lifecycle, generate_id, now_utc


class ScopeLifecycle:
    """Manages scope CRUD and lifecycle transitions."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_scope(
        self,
        *,
        name: str,
        owner_id: str,
        description: str = "",
        parent_scope_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Scope:
        """Create a new scope. Owner is automatically added as an owner-role member."""
        ts = now_utc()
        scope_id = generate_id()
        meta = metadata or {}

        scope = Scope(
            id=scope_id,
            name=name,
            description=description,
            owner_id=owner_id,
            parent_scope_id=parent_scope_id,
            created_at=ts,
            lifecycle=Lifecycle.ACTIVE,
            metadata=meta,
        )

        self._backend.execute(
            "INSERT INTO scopes "
            "(id, name, description, owner_id, parent_scope_id, registry_entry_id, "
            "created_at, lifecycle, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scope.id, scope.name, scope.description, scope.owner_id,
                scope.parent_scope_id, scope.registry_entry_id,
                scope.created_at.isoformat(), _lifecycle_to_db(scope.lifecycle),
                json.dumps(meta),
            ),
        )

        # Auto-add owner as owner-role member
        self._add_membership(
            scope_id=scope_id,
            principal_id=owner_id,
            role=ScopeRole.OWNER,
            granted_by=owner_id,
        )

        if self._audit:
            self._audit.record(
                actor_id=owner_id,
                action=ActionType.SCOPE_CREATE,
                target_type="Scope",
                target_id=scope_id,
                after_state=scope.snapshot(),
            )

        return scope

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_scope(self, scope_id: str) -> Scope | None:
        row = self._backend.fetch_one(
            "SELECT * FROM scopes WHERE id = ?", (scope_id,),
        )
        if row is None:
            return None
        return scope_from_row(row)

    def get_scope_or_raise(self, scope_id: str) -> Scope:
        scope = self.get_scope(scope_id)
        if scope is None:
            raise ScopeNotFoundError(
                f"Scope {scope_id} not found",
                context={"scope_id": scope_id},
            )
        return scope

    # Columns that are safe to ORDER BY
    _SCOPE_ORDER_COLUMNS = {"created_at", "name"}

    def list_scopes(
        self,
        *,
        owner_id: str | None = None,
        parent_scope_id: str | None = None,
        include_archived: bool = False,
        order_by: str = "created_at",
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Scope]:
        """List scopes with optional filtering, ordering, and pagination.

        Args:
            order_by: Column to sort by. Prefix with ``-`` for descending.
                      Allowed: ``created_at``, ``name``. Default: ``created_at``.
            limit: Maximum rows to return. ``None`` means no limit.
            offset: Number of rows to skip.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        if parent_scope_id is not None:
            clauses.append("parent_scope_id = ?")
            params.append(parent_scope_id)
        if not include_archived:
            clauses.append("lifecycle != ?")
            params.append(Lifecycle.ARCHIVED.name)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        # Parse order_by: "-name" → name DESC, "created_at" → created_at ASC
        desc = order_by.startswith("-")
        col = order_by.lstrip("-")
        if col not in self._SCOPE_ORDER_COLUMNS:
            col = "created_at"
        direction = "DESC" if desc else "ASC"

        sql = f"SELECT * FROM scopes{where} ORDER BY {col} {direction}"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        rows = self._backend.fetch_all(sql, tuple(params))
        return [scope_from_row(r) for r in rows]

    def count_scopes(
        self,
        *,
        owner_id: str | None = None,
        parent_scope_id: str | None = None,
        include_archived: bool = False,
    ) -> int:
        """Count scopes matching the given filters."""
        clauses: list[str] = []
        params: list[Any] = []

        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        if parent_scope_id is not None:
            clauses.append("parent_scope_id = ?")
            params.append(parent_scope_id)
        if not include_archived:
            clauses.append("lifecycle != ?")
            params.append(Lifecycle.ARCHIVED.name)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        row = self._backend.fetch_one(
            f"SELECT COUNT(*) as cnt FROM scopes{where}",
            tuple(params),
        )
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def rename_scope(
        self,
        scope_id: str,
        *,
        new_name: str,
        renamed_by: str,
    ) -> Scope:
        """Rename a scope. Raises ScopeFrozenError if scope is frozen/archived."""
        scope = self.get_scope_or_raise(scope_id)
        self._require_mutable(scope)

        old_name = scope.name
        if old_name == new_name:
            return scope

        self._backend.execute(
            "UPDATE scopes SET name = ? WHERE id = ?",
            (new_name, scope_id),
        )

        if self._audit:
            self._audit.record(
                actor_id=renamed_by,
                action=ActionType.SCOPE_MODIFY,
                target_type="Scope",
                target_id=scope_id,
                scope_id=scope_id,
                before_state={"name": old_name},
                after_state={"name": new_name},
            )

        return self.get_scope_or_raise(scope_id)

    def update_scope(
        self,
        scope_id: str,
        *,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        updated_by: str,
    ) -> Scope:
        """Update a scope's description and/or metadata.

        Raises ScopeFrozenError if scope is frozen/archived.
        """
        scope = self.get_scope_or_raise(scope_id)
        self._require_mutable(scope)

        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        sets: list[str] = []
        params: list[Any] = []

        if description is not None and description != scope.description:
            sets.append("description = ?")
            params.append(description)
            before["description"] = scope.description
            after["description"] = description

        if metadata is not None:
            merged = {**scope.metadata, **metadata}
            sets.append("metadata_json = ?")
            params.append(json.dumps(merged))
            before["metadata"] = scope.metadata
            after["metadata"] = merged

        if not sets:
            return scope

        params.append(scope_id)
        self._backend.execute(
            f"UPDATE scopes SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )

        if self._audit:
            self._audit.record(
                actor_id=updated_by,
                action=ActionType.SCOPE_MODIFY,
                target_type="Scope",
                target_id=scope_id,
                scope_id=scope_id,
                before_state=before,
                after_state=after,
            )

        return self.get_scope_or_raise(scope_id)

    # ------------------------------------------------------------------
    # Membership
    # ------------------------------------------------------------------

    def add_member(
        self,
        scope_id: str,
        *,
        principal_id: str,
        role: ScopeRole = ScopeRole.VIEWER,
        granted_by: str,
        expires_at: Any | None = None,
    ) -> ScopeMembership:
        """Add a member to a scope. Raises ScopeFrozenError if scope is frozen/archived."""
        scope = self.get_scope_or_raise(scope_id)
        self._require_mutable(scope)

        return self._add_membership(
            scope_id=scope_id,
            principal_id=principal_id,
            role=role,
            granted_by=granted_by,
            expires_at=expires_at,
        )

    def add_members(
        self,
        scope_id: str,
        *,
        members: list[dict[str, Any]],
        granted_by: str,
    ) -> list[ScopeMembership]:
        """Add multiple members to a scope atomically.

        Each dict in ``members`` must have ``principal_id`` and optionally
        ``role`` (defaults to ``"viewer"``).

        Returns list of ``ScopeMembership`` objects.
        """
        scope = self.get_scope_or_raise(scope_id)
        self._require_mutable(scope)

        results: list[ScopeMembership] = []
        for m in members:
            role = ScopeRole(m.get("role", "viewer"))
            mem = self._add_membership(
                scope_id=scope_id,
                principal_id=m["principal_id"],
                role=role,
                granted_by=granted_by,
            )
            results.append(mem)
        return results

    def _add_membership(
        self,
        *,
        scope_id: str,
        principal_id: str,
        role: ScopeRole,
        granted_by: str,
        expires_at: Any | None = None,
    ) -> ScopeMembership:
        ts = now_utc()

        # Check for previously revoked membership (UNIQUE constraint on scope_id, principal_id, role)
        existing = self._backend.fetch_one(
            "SELECT * FROM scope_memberships "
            "WHERE scope_id = ? AND principal_id = ? AND role = ?",
            (scope_id, principal_id, role.value),
        )

        if existing and existing["lifecycle"] == Lifecycle.ACTIVE.name:
            # Already active — return existing membership
            return membership_from_row(existing)

        if existing:
            # Reactivate the archived membership
            self._backend.execute(
                "UPDATE scope_memberships "
                "SET lifecycle = ?, granted_at = ?, granted_by = ?, expires_at = ? "
                "WHERE scope_id = ? AND principal_id = ? AND role = ?",
                (
                    Lifecycle.ACTIVE.name, ts.isoformat(), granted_by,
                    expires_at.isoformat() if expires_at else None,
                    scope_id, principal_id, role.value,
                ),
            )
            mem = ScopeMembership(
                id=existing["id"],
                scope_id=scope_id,
                principal_id=principal_id,
                role=role,
                granted_at=ts,
                granted_by=granted_by,
                expires_at=expires_at,
            )
        else:
            # Fresh membership
            mem_id = generate_id()
            mem = ScopeMembership(
                id=mem_id,
                scope_id=scope_id,
                principal_id=principal_id,
                role=role,
                granted_at=ts,
                granted_by=granted_by,
                expires_at=expires_at,
            )
            self._backend.execute(
                "INSERT INTO scope_memberships "
                "(id, scope_id, principal_id, role, granted_at, granted_by, expires_at, lifecycle) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    mem.id, mem.scope_id, mem.principal_id, mem.role.value,
                    mem.granted_at.isoformat(), mem.granted_by,
                    mem.expires_at.isoformat() if mem.expires_at else None,
                    mem.lifecycle.name,
                ),
            )

        if self._audit:
            self._audit.record(
                actor_id=granted_by,
                action=ActionType.MEMBERSHIP_CHANGE,
                target_type="Scope",
                target_id=scope_id,
                scope_id=scope_id,
                after_state=mem.snapshot(),
            )

        return mem

    def revoke_member(
        self,
        scope_id: str,
        *,
        principal_id: str,
        revoked_by: str,
        role: ScopeRole | None = None,
    ) -> int:
        """Revoke a member's access (immediate). Returns count of memberships revoked.

        If role is given, only revoke that specific role. Otherwise revoke all roles.
        """
        scope = self.get_scope_or_raise(scope_id)
        self._require_mutable(scope)

        clauses = ["scope_id = ?", "principal_id = ?", "lifecycle = ?"]
        params: list[Any] = [scope_id, principal_id, Lifecycle.ACTIVE.name]

        if role is not None:
            clauses.append("role = ?")
            params.append(role.value)

        where = " AND ".join(clauses)

        # Get before state for audit
        rows = self._backend.fetch_all(
            f"SELECT * FROM scope_memberships WHERE {where}",
            tuple(params),
        )

        if not rows:
            return 0

        self._backend.execute(
            f"UPDATE scope_memberships SET lifecycle = ? WHERE {where}",
            (Lifecycle.ARCHIVED.name, *params),
        )

        if self._audit:
            for row in rows:
                self._audit.record(
                    actor_id=revoked_by,
                    action=ActionType.REVOKE,
                    target_type="Scope",
                    target_id=scope_id,
                    scope_id=scope_id,
                    before_state=membership_from_row(row).snapshot(),
                )

        return len(rows)

    def get_memberships(
        self,
        scope_id: str,
        *,
        active_only: bool = True,
    ) -> list[ScopeMembership]:
        clauses = ["scope_id = ?"]
        params: list[Any] = [scope_id]
        if active_only:
            clauses.append("lifecycle = ?")
            params.append(Lifecycle.ACTIVE.name)

        where = " AND ".join(clauses)
        rows = self._backend.fetch_all(
            f"SELECT * FROM scope_memberships WHERE {where} ORDER BY granted_at ASC",
            tuple(params),
        )
        return [membership_from_row(r) for r in rows]

    def get_principal_scopes(
        self,
        principal_id: str,
        *,
        active_only: bool = True,
    ) -> list[ScopeMembership]:
        """Get all scopes a principal is a member of."""
        clauses = ["principal_id = ?"]
        params: list[Any] = [principal_id]
        if active_only:
            clauses.append("lifecycle = ?")
            params.append(Lifecycle.ACTIVE.name)

        where = " AND ".join(clauses)
        rows = self._backend.fetch_all(
            f"SELECT * FROM scope_memberships WHERE {where}",
            tuple(params),
        )
        return [membership_from_row(r) for r in rows]

    def is_member(
        self,
        scope_id: str,
        principal_id: str,
    ) -> bool:
        """Check if a principal has any active membership in a scope."""
        row = self._backend.fetch_one(
            "SELECT 1 FROM scope_memberships "
            "WHERE scope_id = ? AND principal_id = ? AND lifecycle = ?",
            (scope_id, principal_id, Lifecycle.ACTIVE.name),
        )
        return row is not None

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------

    def freeze_scope(self, scope_id: str, *, frozen_by: str) -> Scope:
        """Freeze a scope — no membership/projection changes allowed."""
        scope = self.get_scope_or_raise(scope_id)
        if not scope.is_active:
            raise ScopeFrozenError(
                f"Scope {scope_id} is not active (current: {scope.lifecycle.name})",
                context={"scope_id": scope_id, "lifecycle": scope.lifecycle.name},
            )

        self._backend.execute(
            "UPDATE scopes SET lifecycle = ? WHERE id = ?",
            (SCOPE_LIFECYCLE_FROZEN, scope_id),
        )

        if self._audit:
            self._audit.record(
                actor_id=frozen_by,
                action=ActionType.LIFECYCLE_CHANGE,
                target_type="Scope",
                target_id=scope_id,
                scope_id=scope_id,
                before_state={"lifecycle": "ACTIVE"},
                after_state={"lifecycle": SCOPE_LIFECYCLE_FROZEN},
            )

        return self.get_scope_or_raise(scope_id)

    def archive_scope(self, scope_id: str, *, archived_by: str) -> Scope:
        """Archive (dissolve) a scope — archives all memberships and projections."""
        scope = self.get_scope_or_raise(scope_id)
        if scope.is_archived:
            raise ScopeFrozenError(
                f"Scope {scope_id} is already archived",
                context={"scope_id": scope_id},
            )

        before_lifecycle = _lifecycle_to_db(scope.lifecycle)

        # Archive all active memberships
        self._backend.execute(
            "UPDATE scope_memberships SET lifecycle = ? "
            "WHERE scope_id = ? AND lifecycle = ?",
            (Lifecycle.ARCHIVED.name, scope_id, Lifecycle.ACTIVE.name),
        )

        # Archive all active projections
        self._backend.execute(
            "UPDATE scope_projections SET lifecycle = ? "
            "WHERE scope_id = ? AND lifecycle = ?",
            (Lifecycle.ARCHIVED.name, scope_id, Lifecycle.ACTIVE.name),
        )

        # Archive the scope itself
        self._backend.execute(
            "UPDATE scopes SET lifecycle = ? WHERE id = ?",
            (Lifecycle.ARCHIVED.name, scope_id),
        )

        if self._audit:
            self._audit.record(
                actor_id=archived_by,
                action=ActionType.SCOPE_DISSOLVE,
                target_type="Scope",
                target_id=scope_id,
                scope_id=scope_id,
                before_state={"lifecycle": before_lifecycle},
                after_state={"lifecycle": Lifecycle.ARCHIVED.name},
            )

        return self.get_scope_or_raise(scope_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_mutable(scope: Scope) -> None:
        """Raise if scope is frozen or archived."""
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
