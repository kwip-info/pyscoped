"""Audit trail retention and compaction policies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa

from scoped.storage._query import compile_for
from scoped.storage._schema import audit_trail
from scoped.storage.interface import StorageBackend


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Policy for managing audit trail size.

    Args:
        max_age_days: Delete entries older than this many days. None = no age limit.
        max_entries: Keep at most this many entries (oldest deleted first). None = no limit.
        compact_before_state: If True, null out before_state for retained entries.
        compact_after_state: If True, null out after_state for retained entries.
    """

    max_age_days: int | None = None
    max_entries: int | None = None
    compact_before_state: bool = False
    compact_after_state: bool = False


@dataclass(frozen=True, slots=True)
class RetentionResult:
    """Result of applying a retention policy."""

    deleted: int = 0
    compacted: int = 0


class AuditRetention:
    """Apply retention and compaction to the audit trail.

    IMPORTANT: Deletion removes audit entries permanently. Hash chain
    integrity is preserved for the remaining entries, but the deleted
    portion can no longer be verified. Use compaction (nulling state
    columns) to save space while preserving the chain.
    """

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def estimate(self, policy: RetentionPolicy) -> int:
        """Estimate how many entries would be affected without applying.

        Returns the count of entries that would be deleted.
        """
        cutoff = self._age_cutoff(policy)
        count = 0

        if cutoff is not None:
            stmt = (
                sa.select(sa.func.count().label("cnt"))
                .select_from(audit_trail)
                .where(audit_trail.c.timestamp < cutoff.isoformat())
            )
            sql, params = compile_for(stmt, self._backend.dialect)
            row = self._backend.fetch_one(sql, params)
            count += row["cnt"] if row else 0

        if policy.max_entries is not None:
            stmt = sa.select(sa.func.count().label("cnt")).select_from(audit_trail)
            sql, params = compile_for(stmt, self._backend.dialect)
            row = self._backend.fetch_one(sql, params)
            total = row["cnt"] if row else 0
            excess = max(0, total - policy.max_entries)
            count = max(count, excess)

        return count

    def apply(self, policy: RetentionPolicy) -> RetentionResult:
        """Apply a retention policy. Returns counts of entries affected.

        Deletions happen first, then compaction on the remaining entries.
        """
        deleted = 0
        compacted = 0

        # Count total before deletions for later arithmetic
        stmt = sa.select(sa.func.count().label("cnt")).select_from(audit_trail)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        total_before = row["cnt"] if row else 0

        # 1. Delete by age
        cutoff = self._age_cutoff(policy)
        if cutoff is not None:
            stmt = sa.delete(audit_trail).where(
                audit_trail.c.timestamp < cutoff.isoformat(),
            )
            sql, params = compile_for(stmt, self._backend.dialect)
            self._backend.execute(sql, params)

        # 2. Delete by max_entries (keep newest N)
        if policy.max_entries is not None:
            # Find the cutoff sequence: keep entries with the highest sequences
            stmt = (
                sa.select(audit_trail.c.sequence)
                .order_by(audit_trail.c.sequence.desc())
                .limit(1)
                .offset(policy.max_entries)
            )
            sql, params = compile_for(stmt, self._backend.dialect)
            row = self._backend.fetch_one(sql, params)
            if row:
                cutoff_seq = row["sequence"]
                stmt = sa.delete(audit_trail).where(
                    audit_trail.c.sequence <= cutoff_seq,
                )
                sql, params = compile_for(stmt, self._backend.dialect)
                self._backend.execute(sql, params)

        # 3. Compact state columns
        if policy.compact_before_state or policy.compact_after_state:
            compacted = self.compact(
                compact_before=policy.compact_before_state,
                compact_after=policy.compact_after_state,
            )

        # Count remaining to compute deletions
        stmt = sa.select(sa.func.count().label("cnt")).select_from(audit_trail)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        remaining = row["cnt"] if row else 0
        deleted = total_before - remaining

        return RetentionResult(deleted=deleted, compacted=compacted)

    def compact(
        self,
        *,
        before_sequence: int | None = None,
        compact_before: bool = True,
        compact_after: bool = True,
    ) -> int:
        """Null out state columns for entries, preserving hashes.

        Args:
            before_sequence: Only compact entries with sequence < this value.
                If None, compact all entries with non-null state.
            compact_before: Null out before_state column.
            compact_after: Null out after_state column.

        Returns:
            Number of entries compacted.
        """
        values: dict[str, Any] = {}
        if compact_before:
            values["before_state"] = None
        if compact_after:
            values["after_state"] = None

        if not values:
            return 0

        # Count candidates first so we can report how many were compacted.
        conditions: list[Any] = []
        if compact_before:
            conditions.append(audit_trail.c.before_state.isnot(None))
        if compact_after:
            conditions.append(audit_trail.c.after_state.isnot(None))

        where_clause = sa.or_(*conditions) if conditions else sa.true()
        if before_sequence is not None:
            where_clause = sa.and_(
                where_clause,
                audit_trail.c.sequence < before_sequence,
            )

        count_stmt = (
            sa.select(sa.func.count().label("cnt"))
            .select_from(audit_trail)
            .where(where_clause)
        )
        sql, params = compile_for(count_stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        affected = row["cnt"] if row else 0

        if affected == 0:
            return 0

        # Perform the update
        update_stmt = sa.update(audit_trail).where(where_clause).values(**values)
        sql, params = compile_for(update_stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        return affected

    @staticmethod
    def _age_cutoff(policy: RetentionPolicy) -> datetime | None:
        if policy.max_age_days is None:
            return None
        return datetime.now(timezone.utc) - timedelta(days=policy.max_age_days)
