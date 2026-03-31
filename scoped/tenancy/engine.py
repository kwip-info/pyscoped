"""Visibility resolution engine — "what can principal X see?"

Walks ownership, scope memberships, projections, and scope hierarchy
to determine what objects are visible to a principal.
"""

from __future__ import annotations

from typing import Any

from scoped.storage.interface import StorageBackend
from scoped.tenancy.models import (
    AccessLevel,
    scope_from_row,
)
from scoped.types import Lifecycle


class VisibilityEngine:
    """Resolve object visibility for a principal.

    Visibility sources (in order):
    1. Objects owned by the principal (always visible)
    2. Objects projected into scopes the principal is an active member of
    3. Objects projected into ancestor scopes (scope hierarchy inheritance)

    Layer 5 (Rules) can further restrict visibility via DENY overrides.
    """

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def visible_object_ids(
        self,
        principal_id: str,
        *,
        object_type: str | None = None,
        limit: int = 1000,
    ) -> list[str]:
        """Return IDs of all objects visible to a principal.

        Combines owned objects + objects projected into member scopes.
        """
        owned = self._owned_object_ids(principal_id, object_type=object_type)
        projected = self._projected_object_ids(principal_id, object_type=object_type)

        # Merge, preserving order and uniqueness
        seen: set[str] = set()
        result: list[str] = []
        for oid in owned + projected:
            if oid not in seen:
                seen.add(oid)
                result.append(oid)
            if len(result) >= limit:
                break

        return result

    def can_see(self, principal_id: str, object_id: str) -> bool:
        """Check if a principal can see a specific object."""
        # Check ownership
        row = self._backend.fetch_one(
            "SELECT 1 FROM scoped_objects "
            "WHERE id = ? AND owner_id = ? AND lifecycle != ?",
            (object_id, principal_id, Lifecycle.ARCHIVED.name),
        )
        if row is not None:
            return True

        # Check scope projections
        row = self._backend.fetch_one(
            "SELECT 1 FROM scope_projections sp "
            "JOIN scope_memberships sm ON sp.scope_id = sm.scope_id "
            "WHERE sp.object_id = ? AND sm.principal_id = ? "
            "AND sp.lifecycle = ? AND sm.lifecycle = ?",
            (object_id, principal_id, Lifecycle.ACTIVE.name, Lifecycle.ACTIVE.name),
        )
        if row is not None:
            return True

        # Check parent scope inheritance
        return self._visible_via_hierarchy(principal_id, object_id)

    def get_access_level(
        self,
        principal_id: str,
        object_id: str,
    ) -> AccessLevel | None:
        """Get the highest access level a principal has for an object.

        Returns None if the object is not visible.
        Owner always gets ADMIN access.
        """
        # Owner gets admin
        row = self._backend.fetch_one(
            "SELECT 1 FROM scoped_objects WHERE id = ? AND owner_id = ?",
            (object_id, principal_id),
        )
        if row is not None:
            return AccessLevel.ADMIN

        # Find highest access via projections
        rows = self._backend.fetch_all(
            "SELECT sp.access_level FROM scope_projections sp "
            "JOIN scope_memberships sm ON sp.scope_id = sm.scope_id "
            "WHERE sp.object_id = ? AND sm.principal_id = ? "
            "AND sp.lifecycle = ? AND sm.lifecycle = ?",
            (object_id, principal_id, Lifecycle.ACTIVE.name, Lifecycle.ACTIVE.name),
        )
        if not rows:
            return None

        # Return highest access level
        levels = [AccessLevel(r["access_level"]) for r in rows]
        priority = {AccessLevel.READ: 0, AccessLevel.WRITE: 1, AccessLevel.ADMIN: 2}
        return max(levels, key=lambda l: priority[l])

    def scope_member_ids(self, scope_id: str) -> list[str]:
        """Get all active member principal IDs for a scope."""
        rows = self._backend.fetch_all(
            "SELECT DISTINCT principal_id FROM scope_memberships "
            "WHERE scope_id = ? AND lifecycle = ?",
            (scope_id, Lifecycle.ACTIVE.name),
        )
        return [r["principal_id"] for r in rows]

    def ancestor_scope_ids(self, scope_id: str, *, max_depth: int = 20) -> list[str]:
        """Walk the scope hierarchy upward, returning ancestor scope IDs."""
        result: list[str] = []
        current = scope_id
        for _ in range(max_depth):
            row = self._backend.fetch_one(
                "SELECT parent_scope_id FROM scopes WHERE id = ?",
                (current,),
            )
            if row is None or row["parent_scope_id"] is None:
                break
            result.append(row["parent_scope_id"])
            current = row["parent_scope_id"]
        return result

    def descendant_scope_ids(self, scope_id: str, *, max_depth: int = 20) -> list[str]:
        """Walk the scope hierarchy downward, returning descendant scope IDs."""
        result: list[str] = []
        queue = [scope_id]
        depth = 0
        while queue and depth < max_depth:
            next_queue: list[str] = []
            for sid in queue:
                rows = self._backend.fetch_all(
                    "SELECT id FROM scopes WHERE parent_scope_id = ? AND lifecycle != ?",
                    (sid, Lifecycle.ARCHIVED.name),
                )
                for r in rows:
                    result.append(r["id"])
                    next_queue.append(r["id"])
            queue = next_queue
            depth += 1
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _owned_object_ids(
        self,
        principal_id: str,
        *,
        object_type: str | None = None,
    ) -> list[str]:
        clauses = ["owner_id = ?", "lifecycle != ?"]
        params: list[Any] = [principal_id, Lifecycle.ARCHIVED.name]
        if object_type:
            clauses.append("object_type = ?")
            params.append(object_type)
        where = " AND ".join(clauses)
        rows = self._backend.fetch_all(
            f"SELECT id FROM scoped_objects WHERE {where}",
            tuple(params),
        )
        return [r["id"] for r in rows]

    def _projected_object_ids(
        self,
        principal_id: str,
        *,
        object_type: str | None = None,
    ) -> list[str]:
        """Get object IDs visible through scope projections (direct memberships)."""
        type_join = ""
        params: list[Any] = [principal_id, Lifecycle.ACTIVE.name, Lifecycle.ACTIVE.name]

        if object_type:
            type_join = "JOIN scoped_objects so ON sp.object_id = so.id "
            params.append(object_type)

        type_filter = "AND so.object_type = ? " if object_type else ""

        sql = (
            "SELECT DISTINCT sp.object_id FROM scope_projections sp "
            "JOIN scope_memberships sm ON sp.scope_id = sm.scope_id "
            f"{type_join}"
            "WHERE sm.principal_id = ? "
            "AND sp.lifecycle = ? AND sm.lifecycle = ? "
            f"{type_filter}"
        )
        rows = self._backend.fetch_all(sql, tuple(params))
        return [r["object_id"] for r in rows]

    def _visible_via_hierarchy(self, principal_id: str, object_id: str) -> bool:
        """Check if object is visible through parent scope inheritance."""
        # Get all scopes the principal is a member of
        member_scopes = self._backend.fetch_all(
            "SELECT scope_id FROM scope_memberships "
            "WHERE principal_id = ? AND lifecycle = ?",
            (principal_id, Lifecycle.ACTIVE.name),
        )
        if not member_scopes:
            return False

        # For each scope, walk up the hierarchy and check projections
        for ms in member_scopes:
            ancestors = self.ancestor_scope_ids(ms["scope_id"])
            for ancestor_id in ancestors:
                row = self._backend.fetch_one(
                    "SELECT 1 FROM scope_projections "
                    "WHERE scope_id = ? AND object_id = ? AND lifecycle = ?",
                    (ancestor_id, object_id, Lifecycle.ACTIVE.name),
                )
                if row is not None:
                    return True

        return False
