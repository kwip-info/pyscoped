"""Objects namespace — versioned, isolated data objects.

Every object in pyscoped is creator-private by default. Only the owner
can read it unless it is explicitly projected into a scope. Every
mutation creates a new immutable version — no in-place updates.

Usage::

    import scoped

    with scoped.as_principal(alice):
        doc, v1 = scoped.objects.create("invoice", data={"amount": 500})
        doc, v2 = scoped.objects.update(doc.id, data={"amount": 600})

        history = scoped.objects.versions(doc.id)
        all_invoices = scoped.objects.list(object_type="invoice")

When a ``ScopedContext`` is active (via ``client.as_principal(...)``),
the ``owner_id`` and ``principal_id`` parameters are inferred
automatically. You can always override them explicitly.
"""

from __future__ import annotations

from typing import Any

from scoped._namespaces._base import _resolve_principal_id


class ObjectsNamespace:
    """Simplified API for scoped object CRUD.

    Wraps ``ScopedManager`` from Layer 3 with context-aware defaults.

    Key methods:
        - ``create(type, data=...)`` — create an object (owner inferred from context)
        - ``get(id)`` — read an object (principal inferred from context)
        - ``update(id, data=...)`` — create a new version
        - ``delete(id)`` — soft-delete (tombstone)
        - ``list()`` — list objects visible to the acting principal
        - ``versions(id)`` — list all versions of an object
    """

    def __init__(self, services: Any) -> None:
        self._svc = services

    def create(
        self,
        object_type: str,
        *,
        data: dict[str, Any],
        owner_id: str | None = None,
        change_reason: str = "created",
    ) -> tuple[Any, Any]:
        """Create a new scoped object.

        The object is creator-private — only the owner can read it until
        it is projected into a scope.

        Args:
            object_type: A string identifying the kind of object
                         (e.g. ``"invoice"``, ``"document"``, ``"config"``).
            data: The object's data as a JSON-serializable dict.
            owner_id: The owning principal's ID. If omitted, inferred
                      from the active ``ScopedContext``.
            change_reason: Human-readable reason for creation.

        Returns:
            A tuple of ``(ScopedObject, ObjectVersion)``. The object
            has ``.id``, ``.object_type``, ``.owner_id``; the version
            has ``.version`` (always 1), ``.data_json``.

        Raises:
            RuntimeError: If no ``owner_id`` given and no context active.

        Example::

            with client.as_principal(alice):
                invoice, v1 = client.objects.create(
                    "invoice", data={"amount": 500, "status": "draft"}
                )
        """
        owner = _resolve_principal_id(owner_id)
        return self._svc.manager.create(
            object_type=object_type,
            owner_id=owner,
            data=data,
            change_reason=change_reason,
        )

    def get(
        self,
        object_id: str,
        *,
        principal_id: str | None = None,
    ) -> Any:
        """Read an object by ID.

        Returns ``None`` if the object does not exist or the principal
        does not have access (isolation enforced).

        Args:
            object_id: The object's unique identifier.
            principal_id: Who is reading. If omitted, inferred from context.

        Returns:
            The ``ScopedObject``, or ``None`` if not found / not authorized.
        """
        pid = _resolve_principal_id(principal_id)
        return self._svc.manager.get(object_id, principal_id=pid)

    def update(
        self,
        object_id: str,
        *,
        data: dict[str, Any],
        principal_id: str | None = None,
        change_reason: str = "",
    ) -> tuple[Any, Any]:
        """Update an object, creating a new immutable version.

        The previous version is preserved — no data is overwritten.

        Args:
            object_id: The object to update.
            data: The complete new state (replaces previous version's data).
            principal_id: Who is updating. If omitted, inferred from context.
            change_reason: Human-readable reason for the change.

        Returns:
            A tuple of ``(ScopedObject, ObjectVersion)`` with the new
            version number.

        Raises:
            AccessDeniedError: If the principal cannot access this object.
        """
        pid = _resolve_principal_id(principal_id)
        return self._svc.manager.update(
            object_id,
            principal_id=pid,
            data=data,
            change_reason=change_reason,
        )

    def delete(
        self,
        object_id: str,
        *,
        principal_id: str | None = None,
        reason: str = "",
    ) -> Any:
        """Soft-delete an object (tombstone).

        The object and all its versions are preserved but marked as
        archived. This is reversible via rollback (Layer 7).

        Args:
            object_id: The object to delete.
            principal_id: Who is deleting. If omitted, inferred from context.
            reason: Human-readable reason for deletion.

        Returns:
            A ``Tombstone`` object.
        """
        pid = _resolve_principal_id(principal_id)
        return self._svc.manager.tombstone(
            object_id, principal_id=pid, reason=reason,
        )

    def list(
        self,
        *,
        principal_id: str | None = None,
        object_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        """List objects visible to the acting principal.

        Only returns objects the principal owns or has access to via
        scope projection.

        Args:
            principal_id: Whose view. If omitted, inferred from context.
            object_type: Filter to this type (e.g. ``"invoice"``).
            limit: Maximum results to return.
            offset: Number of results to skip (for pagination).

        Returns:
            List of ``ScopedObject`` instances.
        """
        pid = _resolve_principal_id(principal_id)
        return self._svc.manager.list_objects(
            principal_id=pid,
            object_type=object_type,
            limit=limit,
            offset=offset,
        )

    def versions(
        self,
        object_id: str,
        *,
        principal_id: str | None = None,
    ) -> list[Any]:
        """List all versions of an object.

        Args:
            object_id: The object whose history to retrieve.
            principal_id: Who is reading. If omitted, inferred from context.

        Returns:
            List of ``ObjectVersion`` instances, ordered by version number.
        """
        pid = _resolve_principal_id(principal_id)
        return self._svc.manager.list_versions(object_id, principal_id=pid)
