"""Scopes namespace — tenancy, sharing, and access control.

Scopes are the sharing mechanism in pyscoped. Every object starts
creator-private. To share it, you project it into a scope. Scope
members can then see projected objects according to their role and
access level.

This namespace merges two internal services:
    - ``ScopeLifecycle`` (create scopes, manage members, lifecycle)
    - ``ProjectionManager`` (project/unproject objects into scopes)

Usage::

    import scoped

    with scoped.as_principal(alice):
        # Create a scope
        team = scoped.scopes.create("Engineering")

        # Add members
        scoped.scopes.add_member(team, bob, role="editor")

        # Share an object
        scoped.scopes.project(doc, team)

        # Bob can now see the document (via scope membership)

All actor parameters (``owner_id``, ``granted_by``, ``projected_by``,
``revoked_by``, ``frozen_by``, ``archived_by``) are inferred from
the active ``ScopedContext`` when not passed explicitly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from scoped._namespaces._base import _resolve_principal_id, _to_id

if TYPE_CHECKING:
    from scoped.tenancy.models import Scope, ScopeMembership, ScopeProjection


class ScopesNamespace:
    """Simplified API for scope management, membership, and projection.

    Wraps ``ScopeLifecycle`` (Layer 4) and ``ProjectionManager`` (Layer 4)
    into a single unified namespace.

    Key methods:
        - ``create(name)`` — create a new scope
        - ``add_member(scope, principal)`` — add a member to a scope
        - ``project(obj, scope)`` — share an object into a scope
        - ``unproject(obj, scope)`` — revoke sharing
        - ``members(scope)`` — list scope members
        - ``freeze(scope)`` / ``archive(scope)`` — lifecycle transitions
    """

    def __init__(self, services: Any) -> None:
        self._svc = services

    # -- Scope CRUD --------------------------------------------------------

    def create(
        self,
        name: str,
        *,
        owner_id: str | None = None,
        description: str = "",
        parent_scope_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Scope:
        """Create a new scope (sharing container).

        Args:
            name: Human-readable scope name (e.g. ``"Engineering"``).
            owner_id: The scope owner. If omitted, inferred from context.
            description: Optional description.
            parent_scope_id: Parent scope ID for hierarchy (nullable).
            metadata: Optional dict of additional metadata.

        Returns:
            A ``Scope`` object with ``.id``, ``.name``, ``.owner_id``.

        Example::

            with client.as_principal(alice):
                team = client.scopes.create("Engineering", description="Eng team")
        """
        owner = _resolve_principal_id(owner_id)
        return self._svc.scopes.create_scope(
            name=name,
            owner_id=owner,
            description=description,
            parent_scope_id=parent_scope_id,
            metadata=metadata,
        )

    def get(self, scope_id: str) -> Scope | None:
        """Get a scope by ID. Returns ``None`` if not found.

        Args:
            scope_id: The scope's unique identifier.

        Returns:
            The ``Scope`` object, or ``None``.
        """
        return self._svc.scopes.get_scope(scope_id)

    def rename(
        self,
        scope: Any,
        new_name: str,
        *,
        renamed_by: str | None = None,
    ) -> Scope:
        """Rename a scope.

        Args:
            scope: The scope (``Scope`` object or string ID).
            new_name: The new name for the scope.
            renamed_by: Who is renaming. If omitted, inferred from context.

        Returns:
            The updated ``Scope`` object.

        Example::

            with client.as_principal(alice):
                client.scopes.rename(team, "Platform Engineering")
        """
        actor = _resolve_principal_id(renamed_by)
        return self._svc.scopes.rename_scope(
            _to_id(scope),
            new_name=new_name,
            renamed_by=actor,
        )

    def update(
        self,
        scope: Any,
        *,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        updated_by: str | None = None,
    ) -> Scope:
        """Update a scope's description and/or metadata.

        Args:
            scope: The scope (object or ID).
            description: New description. ``None`` to leave unchanged.
            metadata: Dict to merge into existing metadata. ``None`` to
                      leave unchanged.
            updated_by: Who is updating. If omitted, inferred from context.

        Returns:
            The updated ``Scope`` object.
        """
        actor = _resolve_principal_id(updated_by)
        return self._svc.scopes.update_scope(
            _to_id(scope),
            description=description,
            metadata=metadata,
            updated_by=actor,
        )

    def list(
        self,
        *,
        owner_id: str | None = None,
        parent_scope_id: str | None = None,
        order_by: str = "created_at",
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Scope]:
        """List scopes, optionally filtered and paginated.

        Args:
            owner_id: Filter to scopes owned by this principal.
            parent_scope_id: Filter to children of this scope.
            order_by: Sort column. Prefix with ``-`` for descending.
                      Allowed: ``created_at``, ``name``. Default: ``created_at``.
            limit: Maximum results. ``None`` for no limit.
            offset: Number of results to skip (for pagination).

        Returns:
            List of ``Scope`` objects.
        """
        return self._svc.scopes.list_scopes(
            owner_id=owner_id,
            parent_scope_id=parent_scope_id,
            order_by=order_by,
            limit=limit,
            offset=offset,
        )

    def count(
        self,
        *,
        owner_id: str | None = None,
        parent_scope_id: str | None = None,
    ) -> int:
        """Count scopes matching the given filters.

        Args:
            owner_id: Filter to scopes owned by this principal.
            parent_scope_id: Filter to children of this scope.

        Returns:
            Total count of matching scopes.
        """
        return self._svc.scopes.count_scopes(
            owner_id=owner_id,
            parent_scope_id=parent_scope_id,
        )

    # -- Membership --------------------------------------------------------

    def add_member(
        self,
        scope: Any,
        principal: Any,
        *,
        role: str = "viewer",
        granted_by: str | None = None,
    ) -> ScopeMembership:
        """Add a principal to a scope with a given role.

        Args:
            scope: The scope (``Scope`` object or string ID).
            principal: The principal to add (``Principal`` object or string ID).
            role: One of ``"viewer"``, ``"editor"``, ``"admin"``, ``"owner"``.
                  Defaults to ``"viewer"``.
            granted_by: Who is granting membership. If omitted, inferred
                        from context.

        Returns:
            A ``ScopeMembership`` object.

        Example::

            with client.as_principal(alice):
                client.scopes.add_member(team, bob, role="editor")
        """
        from scoped.tenancy.models import coerce_role

        actor = _resolve_principal_id(granted_by)
        return self._svc.scopes.add_member(
            _to_id(scope),
            principal_id=_to_id(principal),
            role=coerce_role(role),
            granted_by=actor,
        )

    def add_members(
        self,
        scope: Any,
        members: list[dict[str, Any]],
        *,
        granted_by: str | None = None,
    ) -> list[ScopeMembership]:
        """Add multiple members to a scope at once.

        Each dict must have ``principal_id`` (or a principal object as
        ``principal``) and optionally ``role``.

        Args:
            scope: The scope (object or ID).
            members: List of dicts, e.g.
                     ``[{"principal_id": "...", "role": "editor"}, ...]``
            granted_by: Who is granting. If omitted, inferred from context.

        Returns:
            List of ``ScopeMembership`` objects.
        """
        actor = _resolve_principal_id(granted_by)
        normalized = [
            {
                "principal_id": _to_id(m.get("principal") or m["principal_id"]),
                "role": m.get("role", "viewer"),
            }
            for m in members
        ]
        return self._svc.scopes.add_members(
            _to_id(scope),
            members=normalized,
            granted_by=actor,
        )

    def remove_member(
        self,
        scope: Any,
        principal: Any,
        *,
        revoked_by: str | None = None,
    ) -> int:
        """Remove a principal from a scope.

        Args:
            scope: The scope (object or ID).
            principal: The principal to remove (object or ID).
            revoked_by: Who is revoking. If omitted, inferred from context.

        Returns:
            Number of memberships revoked (usually 1).
        """
        actor = _resolve_principal_id(revoked_by)
        return self._svc.scopes.revoke_member(
            _to_id(scope),
            principal_id=_to_id(principal),
            revoked_by=actor,
        )

    def members(
        self,
        scope: Any,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScopeMembership]:
        """List active members of a scope.

        Args:
            scope: The scope (object or ID).
            limit: Maximum number of members to return.
            offset: Number of members to skip (for pagination).

        Returns:
            List of ``ScopeMembership`` objects with ``.principal_id``
            and ``.role``.
        """
        return self._svc.scopes.get_memberships(
            _to_id(scope), limit=limit, offset=offset,
        )

    # -- Projection (sharing objects into scopes) --------------------------

    def project(
        self,
        obj: Any,
        scope: Any,
        *,
        projected_by: str | None = None,
        access_level: str = "read",
    ) -> ScopeProjection:
        """Share an object into a scope.

        Scope members will be able to see this object at the given
        access level.

        Args:
            obj: The object to share (``ScopedObject`` or string ID).
            scope: The target scope (``Scope`` or string ID).
            projected_by: Who is sharing. If omitted, inferred from context.
            access_level: One of ``"read"``, ``"write"``, ``"admin"``.
                          Defaults to ``"read"``.

        Returns:
            A ``ScopeProjection`` object.

        Example::

            with client.as_principal(alice):
                client.scopes.project(invoice, engineering_team)
        """
        from scoped.tenancy.models import coerce_access_level

        actor = _resolve_principal_id(projected_by)
        return self._svc.projections.project(
            scope_id=_to_id(scope),
            object_id=_to_id(obj),
            projected_by=actor,
            access_level=coerce_access_level(access_level),
        )

    def unproject(
        self,
        obj: Any,
        scope: Any,
        *,
        revoked_by: str | None = None,
    ) -> bool:
        """Revoke an object's projection from a scope.

        Scope members will no longer be able to see this object through
        this scope.

        Args:
            obj: The object (object or ID).
            scope: The scope (object or ID).
            revoked_by: Who is revoking. If omitted, inferred from context.

        Returns:
            ``True`` if the projection was revoked, ``False`` if it
            didn't exist.
        """
        actor = _resolve_principal_id(revoked_by)
        return self._svc.projections.revoke_projection(
            scope_id=_to_id(scope),
            object_id=_to_id(obj),
            revoked_by=actor,
        )

    def projections(
        self,
        scope: Any,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScopeProjection]:
        """List all objects projected into a scope.

        Args:
            scope: The scope (object or ID).
            limit: Maximum number of projections to return.
            offset: Number of projections to skip (for pagination).

        Returns:
            List of ``ScopeProjection`` objects.
        """
        return self._svc.projections.get_projections(
            _to_id(scope), limit=limit, offset=offset,
        )

    # -- Hierarchy ---------------------------------------------------------

    def children(self, scope: Any, *, limit: int = 100) -> list:
        """Get direct child scopes."""
        return self._svc.scopes.children(_to_id(scope), limit=limit)

    def ancestors(self, scope: Any) -> list:
        """Get all ancestor scopes from immediate parent to root."""
        return self._svc.scopes.ancestors(_to_id(scope))

    def descendants(self, scope: Any, *, max_depth: int = 10) -> list:
        """Get all descendant scopes via BFS, bounded by max_depth."""
        return self._svc.scopes.descendants(_to_id(scope), max_depth=max_depth)

    def path(self, scope: Any) -> list:
        """Get the root-to-scope path (ancestors in order, then self)."""
        return self._svc.scopes.path(_to_id(scope))

    # -- Lifecycle ---------------------------------------------------------

    def freeze(self, scope: Any, *, frozen_by: str | None = None) -> Scope:
        """Freeze a scope (no new members or projections allowed).

        Args:
            scope: The scope to freeze (object or ID).
            frozen_by: Who is freezing. If omitted, inferred from context.

        Returns:
            The updated ``Scope`` object.
        """
        actor = _resolve_principal_id(frozen_by)
        return self._svc.scopes.freeze_scope(_to_id(scope), frozen_by=actor)

    def archive(self, scope: Any, *, archived_by: str | None = None) -> Scope:
        """Archive a scope (soft-delete, all memberships revoked).

        Args:
            scope: The scope to archive (object or ID).
            archived_by: Who is archiving. If omitted, inferred from context.

        Returns:
            The updated ``Scope`` object.
        """
        actor = _resolve_principal_id(archived_by)
        return self._svc.scopes.archive_scope(_to_id(scope), archived_by=actor)
