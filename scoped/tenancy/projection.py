"""Object projection into scopes — the explicit sharing act."""

from __future__ import annotations

from typing import Any

from scoped.exceptions import (
    AccessDeniedError,
    ScopeFrozenError,
    ScopeNotFoundError,
)
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
        scope_row = self._backend.fetch_one(
            "SELECT * FROM scopes WHERE id = ?", (scope_id,),
        )
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
        obj_row = self._backend.fetch_one(
            "SELECT * FROM scoped_objects WHERE id = ?", (object_id,),
        )
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
        existing = self._backend.fetch_one(
            "SELECT * FROM scope_projections "
            "WHERE scope_id = ? AND object_id = ?",
            (scope_id, object_id),
        )

        if existing and existing["lifecycle"] == Lifecycle.ACTIVE.name:
            # Already active — return existing projection
            return projection_from_row(existing)

        if existing:
            # Reactivate the archived projection
            self._backend.execute(
                "UPDATE scope_projections "
                "SET lifecycle = ?, access_level = ?, projected_at = ?, projected_by = ? "
                "WHERE scope_id = ? AND object_id = ?",
                (
                    Lifecycle.ACTIVE.name, access_level.value,
                    ts.isoformat(), projected_by,
                    scope_id, object_id,
                ),
            )
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
            self._backend.execute(
                "INSERT INTO scope_projections "
                "(id, scope_id, object_id, projected_at, projected_by, access_level, lifecycle) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    proj.id, proj.scope_id, proj.object_id,
                    proj.projected_at.isoformat(), proj.projected_by,
                    proj.access_level.value, proj.lifecycle.name,
                ),
            )

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
        row = self._backend.fetch_one(
            "SELECT * FROM scope_projections "
            "WHERE scope_id = ? AND object_id = ? AND lifecycle = ?",
            (scope_id, object_id, Lifecycle.ACTIVE.name),
        )
        if row is None:
            return False

        self._backend.execute(
            "UPDATE scope_projections SET lifecycle = ? "
            "WHERE scope_id = ? AND object_id = ? AND lifecycle = ?",
            (Lifecycle.ARCHIVED.name, scope_id, object_id, Lifecycle.ACTIVE.name),
        )

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
        clauses = ["scope_id = ?"]
        params: list[Any] = [scope_id]
        if active_only:
            clauses.append("lifecycle = ?")
            params.append(Lifecycle.ACTIVE.name)

        where = " AND ".join(clauses)
        rows = self._backend.fetch_all(
            f"SELECT * FROM scope_projections WHERE {where} ORDER BY projected_at ASC",
            tuple(params),
        )
        return [projection_from_row(r) for r in rows]

    def get_object_projections(
        self,
        object_id: str,
        *,
        active_only: bool = True,
    ) -> list[ScopeProjection]:
        """Get all scopes an object is projected into."""
        clauses = ["object_id = ?"]
        params: list[Any] = [object_id]
        if active_only:
            clauses.append("lifecycle = ?")
            params.append(Lifecycle.ACTIVE.name)

        where = " AND ".join(clauses)
        rows = self._backend.fetch_all(
            f"SELECT * FROM scope_projections WHERE {where}",
            tuple(params),
        )
        return [projection_from_row(r) for r in rows]

    def is_projected(self, scope_id: str, object_id: str) -> bool:
        """Check if an object has an active projection into a scope."""
        row = self._backend.fetch_one(
            "SELECT 1 FROM scope_projections "
            "WHERE scope_id = ? AND object_id = ? AND lifecycle = ?",
            (scope_id, object_id, Lifecycle.ACTIVE.name),
        )
        return row is not None
