"""Query and verify the audit trail.

Provides filtered reads and hash-chain integrity verification.
Visibility filtering (rule-based) will be layered on once Layer 5
(Rules) is built; for now the query engine returns all matching entries.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from scoped.audit.models import TraceEntry, compute_hash
from scoped.exceptions import TraceIntegrityError
from scoped.storage._query import compile_for
from scoped.storage._schema import audit_trail
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType


class AuditQuery:
    """
    Read-only query interface for the audit trail.

    All results are returned as ``TraceEntry`` instances.
    """

    def __init__(
        self,
        backend: StorageBackend,
        *,
        hash_algorithm: str = "sha256",
    ) -> None:
        self._backend = backend
        self._algorithm = hash_algorithm

    # -- Single-entry lookups -----------------------------------------------

    def get(self, entry_id: str) -> TraceEntry | None:
        """Fetch a single trace entry by ID."""
        stmt = sa.select(audit_trail).where(audit_trail.c.id == entry_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return self._row_to_entry(row) if row else None

    def get_by_sequence(self, sequence: int) -> TraceEntry | None:
        """Fetch a single trace entry by sequence number."""
        stmt = sa.select(audit_trail).where(audit_trail.c.sequence == sequence)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return self._row_to_entry(row) if row else None

    # Columns that are safe to ORDER BY
    _AUDIT_ORDER_COLUMNS = {"sequence", "timestamp"}

    # -- Filtered queries ---------------------------------------------------

    def query(
        self,
        *,
        actor_id: str | None = None,
        action: ActionType | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        scope_id: str | None = None,
        parent_trace_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        order_by: str = "sequence",
        limit: int = 100,
        offset: int = 0,
    ) -> list[TraceEntry]:
        """
        Query the audit trail with optional filters.

        Args:
            order_by: Column to sort by. Prefix with ``-`` for descending.
                      Allowed: ``sequence``, ``timestamp``. Default: ``sequence``.
        """
        stmt = sa.select(audit_trail)

        if actor_id is not None:
            stmt = stmt.where(audit_trail.c.actor_id == actor_id)
        if action is not None:
            stmt = stmt.where(audit_trail.c.action == action.value)
        if target_type is not None:
            stmt = stmt.where(audit_trail.c.target_type == target_type)
        if target_id is not None:
            stmt = stmt.where(audit_trail.c.target_id == target_id)
        if scope_id is not None:
            stmt = stmt.where(audit_trail.c.scope_id == scope_id)
        if parent_trace_id is not None:
            stmt = stmt.where(audit_trail.c.parent_trace_id == parent_trace_id)
        if since is not None:
            stmt = stmt.where(audit_trail.c.timestamp >= since.isoformat())
        if until is not None:
            stmt = stmt.where(audit_trail.c.timestamp <= until.isoformat())

        desc = order_by.startswith("-")
        col = order_by.lstrip("-")
        if col not in self._AUDIT_ORDER_COLUMNS:
            col = "sequence"
        col_ref = audit_trail.c[col]
        stmt = stmt.order_by(col_ref.desc() if desc else col_ref.asc())
        stmt = stmt.limit(limit).offset(offset)

        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [self._row_to_entry(r) for r in rows]

    def count(
        self,
        *,
        actor_id: str | None = None,
        action: ActionType | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> int:
        """Count matching entries."""
        stmt = sa.select(sa.func.count().label("cnt")).select_from(audit_trail)

        if actor_id is not None:
            stmt = stmt.where(audit_trail.c.actor_id == actor_id)
        if action is not None:
            stmt = stmt.where(audit_trail.c.action == action.value)
        if target_type is not None:
            stmt = stmt.where(audit_trail.c.target_type == target_type)
        if target_id is not None:
            stmt = stmt.where(audit_trail.c.target_id == target_id)

        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return row["cnt"] if row else 0

    def children(self, parent_trace_id: str) -> list[TraceEntry]:
        """Get all child traces of a parent trace."""
        return self.query(parent_trace_id=parent_trace_id, limit=1000)

    def history(
        self,
        target_type: str,
        target_id: str,
        *,
        limit: int = 100,
    ) -> list[TraceEntry]:
        """Get the full trace history for a specific target."""
        return self.query(
            target_type=target_type,
            target_id=target_id,
            limit=limit,
        )

    # -- Hash chain verification --------------------------------------------

    def verify_chain(
        self,
        *,
        from_sequence: int = 1,
        to_sequence: int | None = None,
        chunk_size: int = 5000,
    ) -> ChainVerification:
        """
        Verify hash chain integrity over a range of entries.

        Walks the chain from ``from_sequence`` to ``to_sequence``
        (inclusive) and checks that each entry's ``previous_hash``
        matches the preceding entry's ``hash``.

        Processes entries in chunks of ``chunk_size`` to bound memory
        usage on large audit trails.

        Returns a ``ChainVerification`` result.
        """
        total_checked = 0
        first_seq: int | None = None
        last_seq = 0
        prev_hash: str | None = None
        current_from = from_sequence

        while True:
            stmt = sa.select(audit_trail).where(
                audit_trail.c.sequence >= current_from,
            )
            if to_sequence is not None:
                stmt = stmt.where(audit_trail.c.sequence <= to_sequence)
            stmt = stmt.order_by(audit_trail.c.sequence.asc()).limit(chunk_size)

            sql, params = compile_for(stmt, self._backend.dialect)
            rows = self._backend.fetch_all(sql, params)

            if not rows:
                break

            entries = [self._row_to_entry(r) for r in rows]

            for i, entry in enumerate(entries):
                if first_seq is None:
                    first_seq = entry.sequence

                # Recompute hash
                expected = compute_hash(
                    entry_id=entry.id,
                    sequence=entry.sequence,
                    actor_id=entry.actor_id,
                    action=entry.action.value,
                    target_type=entry.target_type,
                    target_id=entry.target_id,
                    timestamp=entry.timestamp.isoformat(),
                    previous_hash=entry.previous_hash,
                    algorithm=self._algorithm,
                )
                if entry.hash != expected:
                    return ChainVerification(
                        valid=False,
                        entries_checked=total_checked + i + 1,
                        first_sequence=first_seq,
                        last_sequence=entry.sequence,
                        broken_at_sequence=entry.sequence,
                    )

                # Check chain link against previous entry
                if prev_hash is not None and entry.previous_hash != prev_hash:
                    return ChainVerification(
                        valid=False,
                        entries_checked=total_checked + i + 1,
                        first_sequence=first_seq,
                        last_sequence=entry.sequence,
                        broken_at_sequence=entry.sequence,
                    )

                prev_hash = entry.hash
                last_seq = entry.sequence

            total_checked += len(entries)

            # If we got fewer than chunk_size, we've reached the end
            if len(entries) < chunk_size:
                break
            current_from = entries[-1].sequence + 1

        if first_seq is None:
            return ChainVerification(
                valid=True, entries_checked=0, first_sequence=0, last_sequence=0,
            )

        return ChainVerification(
            valid=True,
            entries_checked=total_checked,
            first_sequence=first_seq,
            last_sequence=last_seq,
        )

    # -- Row mapping --------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: dict[str, Any]) -> TraceEntry:
        before = row.get("before_state")
        after = row.get("after_state")
        meta = row.get("metadata_json", "{}")

        return TraceEntry(
            id=row["id"],
            sequence=row["sequence"],
            actor_id=row["actor_id"],
            action=ActionType(row["action"]),
            target_type=row["target_type"],
            target_id=row["target_id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            hash=row["hash"],
            previous_hash=row.get("previous_hash", ""),
            scope_id=row.get("scope_id"),
            before_state=json.loads(before) if isinstance(before, str) else before,
            after_state=json.loads(after) if isinstance(after, str) else after,
            metadata=json.loads(meta) if isinstance(meta, str) else (meta or {}),
            parent_trace_id=row.get("parent_trace_id"),
        )


# ---------------------------------------------------------------------------
# Verification result
# ---------------------------------------------------------------------------

class ChainVerification:
    """Result of a hash chain verification."""

    def __init__(
        self,
        *,
        valid: bool,
        entries_checked: int,
        first_sequence: int,
        last_sequence: int,
        broken_at_sequence: int | None = None,
    ) -> None:
        self.valid = valid
        self.entries_checked = entries_checked
        self.first_sequence = first_sequence
        self.last_sequence = last_sequence
        self.broken_at_sequence = broken_at_sequence

    def __bool__(self) -> bool:
        return self.valid

    def __repr__(self) -> str:
        if self.valid:
            return f"ChainVerification(valid=True, checked={self.entries_checked})"
        return (
            f"ChainVerification(valid=False, broken_at={self.broken_at_sequence}, "
            f"checked={self.entries_checked})"
        )
