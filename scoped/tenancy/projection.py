"""Object projection into scopes — the explicit sharing act."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from scoped.exceptions import (
    AccessDeniedError,
    ScopeFrozenError,
    ScopeNotFoundError,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import scope_projections, scoped_objects, scopes
from scoped.storage.interface import StorageBackend
from scoped.tenancy.models import (
    AccessLevel,
    ScopeProjection,
    projection_from_row,
    scope_from_row,
)
from scoped.types import ActionType, Lifecycle, generate_id, now_utc


class ProjectionManager:
    """Manages object projections into scopes.

    Only the object's owner can project it.  Projections are the bridge
    between isolation (Layer 3) and sharing (this layer).
    """

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer

    def project(
        self,
        *,
        scope_id: str,
        object_id: str,
        projected_by: str,
        access_level: AccessLevel = AccessLevel.READ,
    ) -> ScopeProjection:
        """Project an object into a scope.

        The caller must be the object owner (enforced by checking scoped_objects).
        Raises AccessDeniedError if not the owner.
        Raises ScopeNotFoundError if scope doesn't exist.
        Raises ScopeFrozenError if scope is frozen/archived.
        """
        # Verify scope exists and is mutable
        stmt = sa.select(scopes).where(scopes.c.id == scope_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        scope_row = self._backend.fetch_one(sql, params)
        if scope_row is None:
            raise ScopeNotFoundError(
                f"Scope {scope_id} not found",
                context={"scope_id": scope_id},
            )
        scope = scope_from_row(scope_row)
        if scope.is_frozen or scope.is_archived:
            raise ScopeFrozenError(
                f"Scope {scope_id} is not mutable",
                context={"scope_id": scope_id, "lifecycle": scope.lifecycle.name},
            )

        # Verify object exists and caller is owner
        stmt = sa.select(scoped_objects).where(scoped_objects.c.id == object_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        obj_row = self._backend.fetch_one(sql, params)
        if obj_row is None:
            raise AccessDeniedError(
                f"Object {object_id} not found",
                context={"object_id": object_id},
            )
        if obj_row["owner_id"] != projected_by:
            raise AccessDeniedError(
                f"Only the object owner can project objects into scopes",
                context={
                    "object_id": object_id,
                    "owner_id": obj_row["owner_id"],
                    "projected_by": projected_by,
                },
            )

        ts = now_utc()

        # Check for a previously revoked projection (UNIQUE constraint on scope_id, object_id)
        stmt = sa.select(scope_projections).where(
            sa.and_(
                scope_projections.c.scope_id == scope_id,
                scope_projections.c.object_id == object_id,
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        existing = self._backend.fetch_one(sql, params)

        if existing and existing["lifecycle"] == Lifecycle.ACTIVE.name:
            # Already active — return existing projection
            return projection_from_row(existing)

        if existing:
            # Reactivate the archived projection
            update_stmt = sa.update(scope_projections).where(
                sa.and_(
                    scope_projections.c.scope_id == scope_id,
                    scope_projections.c.object_id == object_id,
                )
            ).values(
                lifecycle=Lifecycle.ACTIVE.name,
                access_level=access_level.value,
                projected_at=ts.isoformat(),
                projected_by=projected_by,
            )
            sql, params = compile_for(update_stmt, self._backend.dialect)
            self._backend.execute(sql, params)
            proj = ScopeProjection(
                id=existing["id"],
                scope_id=scope_id,
                object_id=object_id,
                projected_at=ts,
                projected_by=projected_by,
                access_level=access_level,
            )
        else:
            # Fresh projection
            proj_id = generate_id()
            proj = ScopeProjection(
                id=proj_id,
                scope_id=scope_id,
                object_id=object_id,
                projected_at=ts,
                projected_by=projected_by,
                access_level=access_level,
            )
            insert_stmt = sa.insert(scope_projections).values(
                id=proj.id,
                scope_id=proj.scope_id,
                object_id=proj.object_id,
                projected_at=proj.projected_at.isoformat(),
                projected_by=proj.projected_by,
                access_level=proj.access_level.value,
                lifecycle=proj.lifecycle.name,
            )
            sql, params = compile_for(insert_stmt, self._backend.dialect)
            self._backend.execute(sql, params)

        if self._audit:
            self._audit.record(
                actor_id=projected_by,
                action=ActionType.PROJECTION,
                target_type="ScopeProjection",
                target_id=object_id,
                scope_id=scope_id,
                after_state=proj.snapshot(),
            )

        return proj

    def revoke_projection(
        self,
        *,
        scope_id: str,
        object_id: str,
        revoked_by: str,
    ) -> bool:
        """Revoke an object's projection from a scope. Returns True if revoked."""
        active_condition = sa.and_(
            scope_projections.c.scope_id == scope_id,
            scope_projections.c.object_id == object_id,
            scope_projections.c.lifecycle == Lifecycle.ACTIVE.name,
        )
        select_stmt = sa.select(scope_projections).where(active_condition)
        sql, params = compile_for(select_stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            return False

        update_stmt = sa.update(scope_projections).where(active_condition).values(
            lifecycle=Lifecycle.ARCHIVED.name,
        )
        sql, params = compile_for(update_stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit:
            self._audit.record(
                actor_id=revoked_by,
                action=ActionType.REVOKE,
                target_type="ScopeProjection",
                target_id=object_id,
                scope_id=scope_id,
                before_state=projection_from_row(row).snapshot(),
            )

        return True

    def get_projections(
        self,
        scope_id: str,
        *,
        active_only: bool = True,
    ) -> list[ScopeProjection]:
        """Get all projections in a scope."""
        stmt = sa.select(scope_projections).where(
            scope_projections.c.scope_id == scope_id,
        )
        if active_only:
            stmt = stmt.where(scope_projections.c.lifecycle == Lifecycle.ACTIVE.name)
        stmt = stmt.order_by(scope_projections.c.projected_at.asc())

        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [projection_from_row(r) for r in rows]

    def get_object_projections(
        self,
        object_id: str,
        *,
        active_only: bool = True,
    ) -> list[ScopeProjection]:
        """Get all scopes an object is projected into."""
        stmt = sa.select(scope_projections).where(
            scope_projections.c.object_id == object_id,
        )
        if active_only:
            stmt = stmt.where(scope_projections.c.lifecycle == Lifecycle.ACTIVE.name)

        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [projection_from_row(r) for r in rows]

    def is_projected(self, scope_id: str, object_id: str) -> bool:
        """Check if an object has an active projection into a scope."""
        stmt = sa.select(sa.literal(1)).select_from(scope_projections).where(
            sa.and_(
                scope_projections.c.scope_id == scope_id,
                scope_projections.c.object_id == object_id,
                scope_projections.c.lifecycle == Lifecycle.ACTIVE.name,
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return row is not None
