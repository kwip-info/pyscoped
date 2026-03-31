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

from typing import Any

from scoped._namespaces._base import _to_id, _try_resolve_principal_id


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
    ) -> Any:
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

    def get(self, principal_id: str) -> Any:
        """Get a principal by ID.

        Args:
            principal_id: The principal's unique identifier.

        Returns:
            The ``Principal`` object.

        Raises:
            PrincipalNotFoundError: If no principal exists with this ID.
        """
        return self._svc.principals.get_principal(principal_id)

    def find(self, principal_id: str) -> Any:
        """Find a principal by ID, returning ``None`` if not found.

        Args:
            principal_id: The principal's unique identifier.

        Returns:
            The ``Principal`` object, or ``None``.
        """
        return self._svc.principals.find_principal(principal_id)

    def list(self, *, kind: str | None = None) -> list[Any]:
        """List principals, optionally filtered by kind.

        Args:
            kind: Filter to principals of this kind (e.g. ``"user"``).
                  If omitted, returns all principals.

        Returns:
            List of ``Principal`` objects.
        """
        return self._svc.principals.list_principals(kind=kind)
