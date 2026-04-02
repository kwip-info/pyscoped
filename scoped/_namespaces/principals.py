"""Principals namespace — create and manage identities.

Principals represent the actors in your system: users, teams,
organizations, services, or any entity that can own objects and
perform actions.

Usage::

    import scoped

    alice = scoped.principals.create("Alice")
    bob = scoped.principals.create("Bob", kind="user")

    all_users = scoped.principals.list(kind="user")

Every pyscoped operation is attributed to a principal. Use
``client.as_principal(alice)`` to set the acting principal for a block
of operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from scoped._namespaces._base import _resolve_principal_id, _to_id, _try_resolve_principal_id

if TYPE_CHECKING:
    from scoped.identity.principal import Principal


class PrincipalsNamespace:
    """Simplified API for principal (identity) management.

    Wraps ``PrincipalStore`` from Layer 2 with a friendlier interface.

    Key methods:
        - ``create(name)`` — create a new principal
        - ``get(id)`` — get by ID (raises if not found)
        - ``find(id)`` — get by ID (returns None if not found)
        - ``list()`` — list all principals, optionally filtered by kind
    """

    def __init__(self, services: Any) -> None:
        self._svc = services

    def create(
        self,
        display_name: str,
        *,
        kind: str = "user",
        metadata: dict[str, Any] | None = None,
        principal_id: str | None = None,
    ) -> Principal:
        """Create a new principal.

        Args:
            display_name: Human-readable name (e.g. ``"Alice"``).
            kind: Principal type — ``"user"``, ``"team"``, ``"org"``,
                  ``"service"``, or any custom string. Defaults to
                  ``"user"``.
            metadata: Optional dict of additional metadata.
            principal_id: Explicit ID. If omitted, one is generated
                          automatically.

        Returns:
            A ``Principal`` object with ``.id``, ``.kind``,
            ``.display_name``, and ``.metadata`` attributes.

        Example::

            alice = client.principals.create("Alice")
            bot = client.principals.create("CI Bot", kind="service")
        """
        actor = _try_resolve_principal_id() or "system"
        return self._svc.principals.create_principal(
            kind=kind,
            display_name=display_name,
            created_by=actor,
            metadata=metadata,
            principal_id=principal_id,
        )

    def get(self, principal_id: str) -> Principal:
        """Get a principal by ID.

        Args:
            principal_id: The principal's unique identifier.

        Returns:
            The ``Principal`` object.

        Raises:
            PrincipalNotFoundError: If no principal exists with this ID.
        """
        return self._svc.principals.get_principal(principal_id)

    def find(self, principal_id: str) -> Principal | None:
        """Find a principal by ID, returning ``None`` if not found.

        Args:
            principal_id: The principal's unique identifier.

        Returns:
            The ``Principal`` object, or ``None``.
        """
        return self._svc.principals.find_principal(principal_id)

    def update(
        self,
        principal: Any,
        *,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Principal:
        """Update a principal's display name and/or metadata.

        Args:
            principal: The principal (object or ID).
            display_name: New display name. ``None`` to leave unchanged.
            metadata: Dict to merge into existing metadata. ``None`` to
                      leave unchanged.

        Returns:
            The updated ``Principal`` object.
        """
        actor = _try_resolve_principal_id() or "system"
        return self._svc.principals.update_principal(
            _to_id(principal),
            display_name=display_name,
            metadata=metadata,
            updated_by=actor,
        )

    def list(
        self,
        *,
        kind: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Principal]:
        """List principals, optionally filtered by kind.

        Args:
            kind: Filter to principals of this kind (e.g. ``"user"``).
                  If omitted, returns all principals.
            limit: Maximum number of principals to return.
            offset: Number of principals to skip (for pagination).

        Returns:
            List of ``Principal`` objects.
        """
        return self._svc.principals.list_principals(
            kind=kind, limit=limit, offset=offset,
        )

    def archive(self, principal: Any) -> Any:
        """Archive (soft-delete) a principal."""
        from scoped.types import Lifecycle

        pid = _to_id(principal)
        return self._svc.principals.update_lifecycle(pid, Lifecycle.ARCHIVED)

    def add_relationship(
        self,
        parent: Any,
        child: Any,
        *,
        relationship: str = "member_of",
        created_by: str | None = None,
    ) -> Any:
        """Create a directed relationship between two principals."""
        actor = _resolve_principal_id(created_by)
        return self._svc.principals.add_relationship(
            parent_id=_to_id(parent),
            child_id=_to_id(child),
            relationship=relationship,
            created_by=actor,
        )

    def relationships(
        self,
        principal: Any,
        *,
        direction: str = "both",
        relationship: str | None = None,
    ) -> list:
        """Get relationships for a principal."""
        return self._svc.principals.get_relationships(
            _to_id(principal),
            direction=direction,
            relationship=relationship,
        )
