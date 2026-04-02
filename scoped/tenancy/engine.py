"""Visibility resolution engine — "what can principal X see?"

Walks ownership, scope memberships, projections, and scope hierarchy
to determine what objects are visible to a principal.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from scoped.storage._query import compile_for
from scoped.storage._schema import (
    scope_memberships,
    scope_projections,
    scoped_objects,
    scopes,
)
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
        stmt = sa.select(sa.literal(1)).select_from(scoped_objects).where(
            sa.and_(
                scoped_objects.c.id == object_id,
                scoped_objects.c.owner_id == principal_id,
                scoped_objects.c.lifecycle != Lifecycle.ARCHIVED.name,
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is not None:
            return True

        # Check scope projections
        stmt = (
            sa.select(sa.literal(1))
            .select_from(
                scope_projections.join(
                    scope_memberships,
                    scope_projections.c.scope_id == scope_memberships.c.scope_id,
                )
            )
            .where(
                sa.and_(
                    scope_projections.c.object_id == object_id,
                    scope_memberships.c.principal_id == principal_id,
                    scope_projections.c.lifecycle == Lifecycle.ACTIVE.name,
                    scope_memberships.c.lifecycle == Lifecycle.ACTIVE.name,
                )
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
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
        stmt = sa.select(sa.literal(1)).select_from(scoped_objects).where(
            sa.and_(
                scoped_objects.c.id == object_id,
                scoped_objects.c.owner_id == principal_id,
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is not None:
            return AccessLevel.ADMIN

        # Find highest access via projections
        stmt = (
            sa.select(scope_projections.c.access_level)
            .select_from(
                scope_projections.join(
                    scope_memberships,
                    scope_projections.c.scope_id == scope_memberships.c.scope_id,
                )
            )
            .where(
                sa.and_(
                    scope_projections.c.object_id == object_id,
                    scope_memberships.c.principal_id == principal_id,
                    scope_projections.c.lifecycle == Lifecycle.ACTIVE.name,
                    scope_memberships.c.lifecycle == Lifecycle.ACTIVE.name,
                )
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        if not rows:
            return None

        # Return highest access level
        levels = [AccessLevel(r["access_level"]) for r in rows]
        priority = {AccessLevel.READ: 0, AccessLevel.WRITE: 1, AccessLevel.ADMIN: 2}
        return max(levels, key=lambda l: priority[l])

    def scope_member_ids(self, scope_id: str) -> list[str]:
        """Get all active member principal IDs for a scope."""
        stmt = (
            sa.select(sa.distinct(scope_memberships.c.principal_id))
            .where(
                sa.and_(
                    scope_memberships.c.scope_id == scope_id,
                    scope_memberships.c.lifecycle == Lifecycle.ACTIVE.name,
                )
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [r["principal_id"] for r in rows]

    def ancestor_scope_ids(self, scope_id: str, *, max_depth: int = 20) -> list[str]:
        """Walk the scope hierarchy upward, returning ancestor scope IDs.

        Uses a recursive CTE (single query) instead of one query per level.
        """
        depth_col = sa.literal(0).label("depth")
        base = sa.select(
            scopes.c.id, scopes.c.parent_scope_id, depth_col,
        ).where(scopes.c.id == scope_id)

        cte = base.cte(name="ancestors", recursive=True)

        recursive = (
            sa.select(
                scopes.c.id,
                scopes.c.parent_scope_id,
                (cte.c.depth + 1).label("depth"),
            )
            .select_from(scopes.join(cte, scopes.c.id == cte.c.parent_scope_id))
            .where(cte.c.depth < max_depth)
        )
        cte = cte.union_all(recursive)

        stmt = (
            sa.select(cte.c.id)
            .where(cte.c.depth > 0)
            .order_by(cte.c.depth.asc())
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [r["id"] for r in rows]

    def descendant_scope_ids(self, scope_id: str, *, max_depth: int = 20) -> list[str]:
        """Walk the scope hierarchy downward, returning descendant scope IDs.

        Uses a recursive CTE (single query) instead of one query per level.
        """
        depth_col = sa.literal(0).label("depth")
        base = sa.select(scopes.c.id, depth_col).where(scopes.c.id == scope_id)

        cte = base.cte(name="descendants", recursive=True)

        recursive = (
            sa.select(
                scopes.c.id,
                (cte.c.depth + 1).label("depth"),
            )
            .select_from(scopes.join(cte, scopes.c.parent_scope_id == cte.c.id))
            .where(
                sa.and_(
                    scopes.c.lifecycle != Lifecycle.ARCHIVED.name,
                    cte.c.depth < max_depth,
                )
            )
        )
        cte = cte.union_all(recursive)

        stmt = (
            sa.select(cte.c.id)
            .where(cte.c.depth > 0)
            .order_by(cte.c.depth.asc())
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [r["id"] for r in rows]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _owned_object_ids(
        self,
        principal_id: str,
        *,
        object_type: str | None = None,
    ) -> list[str]:
        stmt = sa.select(scoped_objects.c.id).where(
            sa.and_(
                scoped_objects.c.owner_id == principal_id,
                scoped_objects.c.lifecycle != Lifecycle.ARCHIVED.name,
            )
        )
        if object_type:
            stmt = stmt.where(scoped_objects.c.object_type == object_type)

        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [r["id"] for r in rows]

    def _projected_object_ids(
        self,
        principal_id: str,
        *,
        object_type: str | None = None,
    ) -> list[str]:
        """Get object IDs visible through scope projections (direct memberships)."""
        join_clause = scope_projections.join(
            scope_memberships,
            scope_projections.c.scope_id == scope_memberships.c.scope_id,
        )
        if object_type:
            join_clause = join_clause.join(
                scoped_objects,
                scope_projections.c.object_id == scoped_objects.c.id,
            )

        stmt = (
            sa.select(sa.distinct(scope_projections.c.object_id))
            .select_from(join_clause)
            .where(
                sa.and_(
                    scope_memberships.c.principal_id == principal_id,
                    scope_projections.c.lifecycle == Lifecycle.ACTIVE.name,
                    scope_memberships.c.lifecycle == Lifecycle.ACTIVE.name,
                )
            )
        )
        if object_type:
            stmt = stmt.where(scoped_objects.c.object_type == object_type)

        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [r["object_id"] for r in rows]

    def _visible_via_hierarchy(self, principal_id: str, object_id: str) -> bool:
        """Check if object is visible through parent scope inheritance.

        Uses a recursive CTE to walk all ancestor scopes for the
        principal's memberships in a single query, then checks projections.
        """
        # Base: all scopes the principal is an active member of
        depth_col = sa.literal(0).label("depth")
        base = (
            sa.select(scope_memberships.c.scope_id, depth_col)
            .where(
                sa.and_(
                    scope_memberships.c.principal_id == principal_id,
                    scope_memberships.c.lifecycle == Lifecycle.ACTIVE.name,
                )
            )
        )
        cte = base.cte(name="member_ancestors", recursive=True)

        # Recursive: walk up parent_scope_id
        recursive = (
            sa.select(
                scopes.c.parent_scope_id.label("scope_id"),
                (cte.c.depth + 1).label("depth"),
            )
            .select_from(scopes.join(cte, scopes.c.id == cte.c.scope_id))
            .where(
                sa.and_(
                    scopes.c.parent_scope_id.isnot(None),
                    cte.c.depth < 20,
                )
            )
        )
        cte = cte.union_all(recursive)

        # Check if any projection matches an ancestor scope
        ancestor_ids = sa.select(cte.c.scope_id).where(cte.c.depth > 0)
        stmt = (
            sa.select(sa.literal(1))
            .select_from(scope_projections)
            .where(
                sa.and_(
                    scope_projections.c.object_id == object_id,
                    scope_projections.c.lifecycle == Lifecycle.ACTIVE.name,
                    scope_projections.c.scope_id.in_(ancestor_ids),
                )
            )
            .limit(1)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return row is not None
