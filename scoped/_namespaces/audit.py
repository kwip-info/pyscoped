"""Audit namespace — query the tamper-evident audit trail.

Every operation in pyscoped produces a hash-chained audit entry.
This namespace provides query access to those entries and chain
verification.

Usage::

    import scoped

    trail = scoped.audit.for_object(doc.id)
    user_actions = scoped.audit.for_principal(alice.id, limit=50)
    verification = scoped.audit.verify()
    assert verification.valid

The audit trail is append-only and immutable. Each entry contains:
    - Who acted (``actor_id``)
    - What they did (``action``)
    - What they acted on (``target_type``, ``target_id``)
    - Before/after state snapshots
    - A SHA-256 hash linking to the previous entry (tamper-evident chain)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from scoped.audit.models import TraceEntry
    from scoped.audit.query import ChainVerification


class AuditNamespace:
    """Simplified API for querying the audit trail.

    Wraps ``AuditQuery`` from Layer 6 with convenience methods.

    Key methods:
        - ``for_object(id)`` — all audit entries for an object
        - ``for_principal(id)`` — all actions by a principal
        - ``for_scope(id)`` — all actions within a scope
        - ``query(**filters)`` — flexible multi-filter query
        - ``verify()`` — verify the hash chain integrity
    """

    def __init__(self, services: Any) -> None:
        self._svc = services
        self._query: Any = None

    @property
    def _audit_query(self) -> Any:
        if self._query is None:
            from scoped.audit.query import AuditQuery

            self._query = AuditQuery(self._svc.backend)
        return self._query

    def for_object(self, object_id: str, *, limit: int = 100) -> list[TraceEntry]:
        """Get audit entries for a specific object.

        Returns all actions that targeted this object (create, update,
        tombstone, project, etc.) in reverse chronological order.

        Args:
            object_id: The object's unique identifier.
            limit: Maximum entries to return.

        Returns:
            List of ``TraceEntry`` objects.

        Example::

            trail = client.audit.for_object(invoice.id)
            for entry in trail:
                print(f"{entry.actor_id} {entry.action} at {entry.timestamp}")
        """
        return self._audit_query.query(target_id=object_id, limit=limit)

    def for_principal(self, principal_id: str, *, limit: int = 100) -> list[TraceEntry]:
        """Get audit entries for actions performed by a principal.

        Args:
            principal_id: The actor's unique identifier.
            limit: Maximum entries to return.

        Returns:
            List of ``TraceEntry`` objects.
        """
        return self._audit_query.query(actor_id=principal_id, limit=limit)

    def for_scope(self, scope_id: str, *, limit: int = 100) -> list[TraceEntry]:
        """Get audit entries within a scope.

        Args:
            scope_id: The scope's unique identifier.
            limit: Maximum entries to return.

        Returns:
            List of ``TraceEntry`` objects.
        """
        return self._audit_query.query(scope_id=scope_id, limit=limit)

    def query(self, **kwargs: Any) -> list[TraceEntry]:
        """Flexible audit query with multiple filters.

        Supports all filters available on ``AuditQuery.query()``:
        ``actor_id``, ``action``, ``target_type``, ``target_id``,
        ``scope_id``, ``since``, ``until``, ``limit``, ``offset``.

        Args:
            **kwargs: Filter parameters passed through to ``AuditQuery.query()``.

        Returns:
            List of ``TraceEntry`` objects.

        Example::

            entries = client.audit.query(
                actor_id=alice.id,
                action="create",
                since=datetime(2026, 1, 1),
                limit=50,
            )
        """
        return self._audit_query.query(**kwargs)

    def verify(
        self,
        *,
        from_sequence: int = 1,
        to_sequence: int | None = None,
    ) -> ChainVerification:
        """Verify the integrity of the audit hash chain.

        Each audit entry contains a SHA-256 hash of itself and a
        reference to the previous entry's hash. This method walks the
        chain and verifies that no entries have been tampered with,
        inserted, or deleted.

        Args:
            from_sequence: Start verification from this sequence number.
            to_sequence: End verification at this sequence number.
                         If omitted, verifies through the latest entry.

        Returns:
            A ``ChainVerification`` object with ``.valid`` (bool),
            ``.entries_checked`` (int), and ``.broken_at_sequence``
            (int or None).

        Example::

            result = client.audit.verify()
            assert result.valid, f"Chain broken at {result.broken_at_sequence}"
        """
        return self._audit_query.verify_chain(
            from_sequence=from_sequence,
            to_sequence=to_sequence,
        )
