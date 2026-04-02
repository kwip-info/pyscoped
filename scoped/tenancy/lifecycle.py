"""Scope lifecycle management — create, freeze, archive, dissolve."""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from scoped.exceptions import (
    AccessDeniedError,
    ScopeFrozenError,
    ScopeNotFoundError,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import scope_memberships, scope_projections, scopes
from scoped.storage.interface import StorageBackend
from scoped.tenancy.models import (
    SCOPE_LIFECYCLE_FROZEN,
    Scope,
    ScopeMembership,
    ScopeRole,
    _lifecycle_to_db,
    active_membership_condition,
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

        stmt = sa.insert(scopes).values(
            id=scope.id,
            name=scope.name,
            description=scope.description,
            owner_id=scope.owner_id,
            parent_scope_id=scope.parent_scope_id,
            registry_entry_id=scope.registry_entry_id,
            created_at=scope.created_at.isoformat(),
            lifecycle=_lifecycle_to_db(scope.lifecycle),
            metadata_json=json.dumps(meta),
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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
        stmt = sa.select(scopes).where(scopes.c.id == scope_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
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
        limit: int = 1000,
        offset: int = 0,
    ) -> list[Scope]:
        """List scopes with optional filtering, ordering, and pagination.

        Args:
            order_by: Column to sort by. Prefix with ``-`` for descending.
                      Allowed: ``created_at``, ``name``. Default: ``created_at``.
            limit: Maximum rows to return. ``None`` means no limit.
            offset: Number of rows to skip.
        """
        stmt = sa.select(scopes)

        if owner_id is not None:
            stmt = stmt.where(scopes.c.owner_id == owner_id)
        if parent_scope_id is not None:
            stmt = stmt.where(scopes.c.parent_scope_id == parent_scope_id)
        if not include_archived:
            stmt = stmt.where(scopes.c.lifecycle != Lifecycle.ARCHIVED.name)

        # Parse order_by: "-name" → name DESC, "created_at" → created_at ASC
        desc = order_by.startswith("-")
        col = order_by.lstrip("-")
        if col not in self._SCOPE_ORDER_COLUMNS:
            col = "created_at"
        order_col = scopes.c[col]
        stmt = stmt.order_by(order_col.desc() if desc else order_col.asc())

        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)

        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [scope_from_row(r) for r in rows]

    def count_scopes(
        self,
        *,
        owner_id: str | None = None,
        parent_scope_id: str | None = None,
        include_archived: bool = False,
    ) -> int:
        """Count scopes matching the given filters."""
        stmt = sa.select(sa.func.count().label("cnt")).select_from(scopes)

        if owner_id is not None:
            stmt = stmt.where(scopes.c.owner_id == owner_id)
        if parent_scope_id is not None:
            stmt = stmt.where(scopes.c.parent_scope_id == parent_scope_id)
        if not include_archived:
            stmt = stmt.where(scopes.c.lifecycle != Lifecycle.ARCHIVED.name)

        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
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

        stmt = sa.update(scopes).where(scopes.c.id == scope_id).values(name=new_name)
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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
        values: dict[str, Any] = {}

        if description is not None and description != scope.description:
            values["description"] = description
            before["description"] = scope.description
            after["description"] = description

        if metadata is not None:
            merged = {**scope.metadata, **metadata}
            values["metadata_json"] = json.dumps(merged)
            before["metadata"] = scope.metadata
            after["metadata"] = merged

        if not values:
            return scope

        stmt = sa.update(scopes).where(scopes.c.id == scope_id).values(**values)
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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

        # Read + conditional write in a single transaction to prevent races
        with self._backend.transaction() as txn:
            select_stmt = sa.select(scope_memberships).where(
                sa.and_(
                    scope_memberships.c.scope_id == scope_id,
                    scope_memberships.c.principal_id == principal_id,
                    scope_memberships.c.role == role.value,
                )
            )
            sql, params = compile_for(select_stmt, self._backend.dialect)
            existing = txn.fetch_one(sql, params)

            if existing and existing["lifecycle"] == Lifecycle.ACTIVE.name:
                # Already active — return existing membership
                txn.commit()
                return membership_from_row(existing)

            if existing:
                # Reactivate the archived membership
                update_stmt = sa.update(scope_memberships).where(
                    sa.and_(
                        scope_memberships.c.scope_id == scope_id,
                        scope_memberships.c.principal_id == principal_id,
                        scope_memberships.c.role == role.value,
                    )
                ).values(
                    lifecycle=Lifecycle.ACTIVE.name,
                    granted_at=ts.isoformat(),
                    granted_by=granted_by,
                    expires_at=expires_at.isoformat() if expires_at else None,
                )
                sql, params = compile_for(update_stmt, self._backend.dialect)
                txn.execute(sql, params)
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
                insert_stmt = sa.insert(scope_memberships).values(
                    id=mem.id,
                    scope_id=mem.scope_id,
                    principal_id=mem.principal_id,
                    role=mem.role.value,
                    granted_at=mem.granted_at.isoformat(),
                    granted_by=mem.granted_by,
                    expires_at=mem.expires_at.isoformat() if mem.expires_at else None,
                    lifecycle=mem.lifecycle.name,
                )
                sql, params = compile_for(insert_stmt, self._backend.dialect)
                txn.execute(sql, params)
            txn.commit()

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

        conditions = sa.and_(
            scope_memberships.c.scope_id == scope_id,
            scope_memberships.c.principal_id == principal_id,
            scope_memberships.c.lifecycle == Lifecycle.ACTIVE.name,
        )
        if role is not None:
            conditions = sa.and_(conditions, scope_memberships.c.role == role.value)

        # Get before state for audit
        select_stmt = sa.select(scope_memberships).where(conditions)
        sql, params = compile_for(select_stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)

        if not rows:
            return 0

        update_stmt = sa.update(scope_memberships).where(conditions).values(
            lifecycle=Lifecycle.ARCHIVED.name,
        )
        sql, params = compile_for(update_stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ScopeMembership]:
        stmt = sa.select(scope_memberships).where(
            scope_memberships.c.scope_id == scope_id,
        )
        if active_only:
            stmt = stmt.where(active_membership_condition(scope_memberships))
        stmt = stmt.order_by(scope_memberships.c.granted_at.asc())

        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)

        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [membership_from_row(r) for r in rows]

    def get_principal_scopes(
        self,
        principal_id: str,
        *,
        active_only: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ScopeMembership]:
        """Get all scopes a principal is a member of."""
        stmt = sa.select(scope_memberships).where(
            scope_memberships.c.principal_id == principal_id,
        )
        if active_only:
            stmt = stmt.where(active_membership_condition(scope_memberships))

        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)

        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [membership_from_row(r) for r in rows]

    def is_member(
        self,
        scope_id: str,
        principal_id: str,
    ) -> bool:
        """Check if a principal has any active, non-expired membership in a scope."""
        stmt = sa.select(sa.literal(1)).select_from(scope_memberships).where(
            scope_memberships.c.scope_id == scope_id,
            scope_memberships.c.principal_id == principal_id,
            active_membership_condition(scope_memberships),
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is not None:
            return True

        # Lazy archival: check if membership exists but expired
        expired_stmt = sa.select(sa.literal(1)).select_from(scope_memberships).where(
            scope_memberships.c.scope_id == scope_id,
            scope_memberships.c.principal_id == principal_id,
            scope_memberships.c.lifecycle == "ACTIVE",
            scope_memberships.c.expires_at.isnot(None),
            scope_memberships.c.expires_at <= now_utc().isoformat(),
        )
        sql, params = compile_for(expired_stmt, self._backend.dialect)
        expired_row = self._backend.fetch_one(sql, params)
        if expired_row is not None:
            # Archive the expired membership
            archive_stmt = sa.update(scope_memberships).where(
                scope_memberships.c.scope_id == scope_id,
                scope_memberships.c.principal_id == principal_id,
                scope_memberships.c.lifecycle == "ACTIVE",
                scope_memberships.c.expires_at.isnot(None),
                scope_memberships.c.expires_at <= now_utc().isoformat(),
            ).values(lifecycle="ARCHIVED")
            sql, params = compile_for(archive_stmt, self._backend.dialect)
            self._backend.execute(sql, params)

        return False

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

        with self._backend.transaction() as txn:
            stmt = sa.update(scopes).where(scopes.c.id == scope_id).values(
                lifecycle=SCOPE_LIFECYCLE_FROZEN,
            )
            sql, params = compile_for(stmt, self._backend.dialect)
            txn.execute(sql, params)
            txn.commit()

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
        """Archive (dissolve) a scope — archives all memberships and projections.

        All three updates (memberships, projections, scope) are executed
        atomically within a single transaction.
        """
        scope = self.get_scope_or_raise(scope_id)
        if scope.is_archived:
            raise ScopeFrozenError(
                f"Scope {scope_id} is already archived",
                context={"scope_id": scope_id},
            )

        before_lifecycle = _lifecycle_to_db(scope.lifecycle)

        with self._backend.transaction() as txn:
            # Archive all active memberships
            stmt1 = sa.update(scope_memberships).where(
                sa.and_(
                    scope_memberships.c.scope_id == scope_id,
                    scope_memberships.c.lifecycle == Lifecycle.ACTIVE.name,
                )
            ).values(lifecycle=Lifecycle.ARCHIVED.name)
            sql, params = compile_for(stmt1, self._backend.dialect)
            txn.execute(sql, params)

            # Archive all active projections
            stmt2 = sa.update(scope_projections).where(
                sa.and_(
                    scope_projections.c.scope_id == scope_id,
                    scope_projections.c.lifecycle == Lifecycle.ACTIVE.name,
                )
            ).values(lifecycle=Lifecycle.ARCHIVED.name)
            sql, params = compile_for(stmt2, self._backend.dialect)
            txn.execute(sql, params)

            # Archive the scope itself
            stmt3 = sa.update(scopes).where(scopes.c.id == scope_id).values(
                lifecycle=Lifecycle.ARCHIVED.name,
            )
            sql, params = compile_for(stmt3, self._backend.dialect)
            txn.execute(sql, params)

            txn.commit()

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
    # Hierarchy
    # ------------------------------------------------------------------

    def children(self, scope_id: str, *, limit: int = 100) -> list[Scope]:
        """Get direct child scopes."""
        stmt = sa.select(scopes).where(
            scopes.c.parent_scope_id == scope_id,
            scopes.c.lifecycle != "ARCHIVED",
        ).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [scope_from_row(r) for r in rows]

    def ancestors(self, scope_id: str) -> list[Scope]:
        """Get all ancestor scopes from immediate parent to root."""
        result: list[Scope] = []
        current_id: str | None = scope_id
        seen: set[str] = set()
        while current_id:
            if current_id in seen:
                break  # cycle protection
            seen.add(current_id)
            scope = self.get_scope(current_id)
            if scope is None:
                break
            if current_id != scope_id:  # don't include self
                result.append(scope)
            current_id = scope.parent_scope_id
        return result

    def descendants(self, scope_id: str, *, max_depth: int = 10) -> list[Scope]:
        """Get all descendant scopes via BFS, bounded by max_depth."""
        result: list[Scope] = []
        queue: list[tuple[str, int]] = [(scope_id, 0)]
        seen: set[str] = {scope_id}
        while queue:
            current_id, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            kids = self.children(current_id, limit=1000)
            for child in kids:
                if child.id not in seen:
                    seen.add(child.id)
                    result.append(child)
                    queue.append((child.id, depth + 1))
        return result

    def path(self, scope_id: str) -> list[Scope]:
        """Get the root-to-scope path (ancestors in order, then self)."""
        ancestors = self.ancestors(scope_id)
        ancestors.reverse()  # root first
        scope = self.get_scope(scope_id)
        if scope:
            ancestors.append(scope)
        return ancestors

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
