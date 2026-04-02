"""Append-only audit trail writer with hash chaining.

The writer is the single entry point for creating trace entries.  It
manages sequence numbering, hash chaining, and persistence.  All writes
go through here — nothing else touches the audit_trail table directly.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from typing import Any

from scoped.audit.models import TraceEntry, compute_hash
from scoped.exceptions import AuditSequenceCollisionError
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, generate_id, now_utc

logger = logging.getLogger(__name__)

_MAX_SEQUENCE_RETRIES = 3


class AuditWriter:
    """
    Append-only writer for the audit trail.

    Thread-safe.  Maintains the hash chain by tracking the last hash
    and sequence number in memory, seeded from the database on init.

    Usage::

        writer = AuditWriter(backend)
        entry = writer.record(
            actor_id="user-123",
            action=ActionType.CREATE,
            target_type="Document",
            target_id="doc-456",
        )
    """

    def __init__(
        self,
        backend: StorageBackend,
        *,
        hash_algorithm: str = "sha256",
    ) -> None:
        self._backend = backend
        self._algorithm = hash_algorithm
        self._lock = threading.Lock()

        # Seed from database
        self._sequence, self._last_hash = self._seed_chain()

    def _seed_chain(self) -> tuple[int, str]:
        """Read the latest sequence and hash from the database."""
        row = self._backend.fetch_one(
            "SELECT sequence, hash FROM audit_trail ORDER BY sequence DESC LIMIT 1"
        )
        if row is None:
            return 0, ""
        return row["sequence"], row["hash"]

    def _reseed_if_stale(self) -> None:
        """Re-read the latest sequence from DB if another process advanced it.

        In multi-process deployments (e.g. gunicorn workers sharing a
        Postgres database), another process may have written entries since
        this writer was initialized. Re-seeding under the lock prevents
        sequence collisions.
        """
        db_seq, db_hash = self._seed_chain()
        if db_seq > self._sequence:
            self._sequence = db_seq
            self._last_hash = db_hash

    def record(
        self,
        *,
        actor_id: str,
        action: ActionType,
        target_type: str,
        target_id: str,
        scope_id: str | None = None,
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        parent_trace_id: str | None = None,
    ) -> TraceEntry:
        """
        Record a single trace entry.

        This is the primary API.  It assigns a sequence number, computes
        the hash chain link, persists the entry, and returns it.

        Uses a database transaction with bounded retry to handle
        multi-process sequence collisions (e.g. gunicorn workers).
        """
        with self._lock:
            for attempt in range(_MAX_SEQUENCE_RETRIES + 1):
                self._reseed_if_stale()

                entry_id = generate_id()
                ts = now_utc()
                seq = self._sequence + 1

                entry_hash = compute_hash(
                    entry_id=entry_id,
                    sequence=seq,
                    actor_id=actor_id,
                    action=action.value,
                    target_type=target_type,
                    target_id=target_id,
                    timestamp=ts.isoformat(),
                    previous_hash=self._last_hash,
                    algorithm=self._algorithm,
                )

                entry = TraceEntry(
                    id=entry_id,
                    sequence=seq,
                    actor_id=actor_id,
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    timestamp=ts,
                    hash=entry_hash,
                    previous_hash=self._last_hash,
                    scope_id=scope_id,
                    before_state=before_state,
                    after_state=after_state,
                    metadata=metadata or {},
                    parent_trace_id=parent_trace_id,
                )

                try:
                    with self._backend.transaction() as txn:
                        self._persist_in_txn(txn, entry)
                        txn.commit()
                except Exception as exc:
                    if self._is_sequence_collision(exc) and attempt < _MAX_SEQUENCE_RETRIES:
                        logger.warning(
                            "Audit sequence collision at seq=%d, retrying (%d/%d)",
                            seq, attempt + 1, _MAX_SEQUENCE_RETRIES,
                        )
                        continue
                    raise

                self._sequence = seq
                self._last_hash = entry_hash
                return entry

            raise AuditSequenceCollisionError(
                f"Failed to assign unique audit sequence after "
                f"{_MAX_SEQUENCE_RETRIES} retries",
                context={"last_attempted_sequence": seq},
            )

    def record_batch(
        self,
        entries: list[dict[str, Any]],
    ) -> list[TraceEntry]:
        """
        Record multiple trace entries atomically.

        Each dict in ``entries`` should contain the same kwargs as
        ``record()``.  Useful for nested operations that produce
        multiple traces.

        Retries on sequence collision with in-memory state rollback.
        """
        with self._lock:
            for attempt in range(_MAX_SEQUENCE_RETRIES + 1):
                self._reseed_if_stale()
                ts = now_utc()

                # Save state for rollback on failure
                saved_seq = self._sequence
                saved_hash = self._last_hash
                batch_results: list[TraceEntry] = []

                try:
                    with self._backend.transaction() as txn:
                        for entry_kwargs in entries:
                            entry_id = generate_id()
                            seq = self._sequence + 1

                            action = entry_kwargs["action"]
                            action_val = action.value if isinstance(action, ActionType) else action

                            entry_hash = compute_hash(
                                entry_id=entry_id,
                                sequence=seq,
                                actor_id=entry_kwargs["actor_id"],
                                action=action_val,
                                target_type=entry_kwargs["target_type"],
                                target_id=entry_kwargs["target_id"],
                                timestamp=ts.isoformat(),
                                previous_hash=self._last_hash,
                                algorithm=self._algorithm,
                            )

                            entry = TraceEntry(
                                id=entry_id,
                                sequence=seq,
                                actor_id=entry_kwargs["actor_id"],
                                action=action if isinstance(action, ActionType) else ActionType(action),
                                target_type=entry_kwargs["target_type"],
                                target_id=entry_kwargs["target_id"],
                                timestamp=ts,
                                hash=entry_hash,
                                previous_hash=self._last_hash,
                                scope_id=entry_kwargs.get("scope_id"),
                                before_state=entry_kwargs.get("before_state"),
                                after_state=entry_kwargs.get("after_state"),
                                metadata=entry_kwargs.get("metadata", {}),
                                parent_trace_id=entry_kwargs.get("parent_trace_id"),
                            )

                            self._persist_in_txn(txn, entry)
                            self._sequence = seq
                            self._last_hash = entry_hash
                            batch_results.append(entry)

                        txn.commit()
                    return batch_results
                except Exception as exc:
                    # Rollback in-memory state
                    self._sequence = saved_seq
                    self._last_hash = saved_hash
                    if self._is_sequence_collision(exc) and attempt < _MAX_SEQUENCE_RETRIES:
                        logger.warning(
                            "Audit batch sequence collision, retrying (%d/%d)",
                            attempt + 1, _MAX_SEQUENCE_RETRIES,
                        )
                        continue
                    raise

            raise AuditSequenceCollisionError(
                f"Failed to assign unique audit sequence for batch after "
                f"{_MAX_SEQUENCE_RETRIES} retries",
            )

    @property
    def last_sequence(self) -> int:
        return self._sequence

    @property
    def last_hash(self) -> str:
        return self._last_hash

    # -- Persistence --------------------------------------------------------

    def _persist(self, entry: TraceEntry) -> None:
        """Persist a single entry outside a transaction (auto-commit)."""
        self._backend.execute(
            """INSERT INTO audit_trail
               (id, sequence, actor_id, action, target_type, target_id,
                scope_id, timestamp, before_state, after_state,
                metadata_json, parent_trace_id, hash, previous_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            self._entry_params(entry),
        )

    def _persist_in_txn(self, txn: Any, entry: TraceEntry) -> None:
        """Persist a single entry within an existing transaction."""
        txn.execute(
            """INSERT INTO audit_trail
               (id, sequence, actor_id, action, target_type, target_id,
                scope_id, timestamp, before_state, after_state,
                metadata_json, parent_trace_id, hash, previous_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            self._entry_params(entry),
        )

    @staticmethod
    def _entry_params(entry: TraceEntry) -> tuple[Any, ...]:
        return (
            entry.id,
            entry.sequence,
            entry.actor_id,
            entry.action.value,
            entry.target_type,
            entry.target_id,
            entry.scope_id,
            entry.timestamp.isoformat(),
            json.dumps(entry.before_state) if entry.before_state is not None else None,
            json.dumps(entry.after_state) if entry.after_state is not None else None,
            json.dumps(entry.metadata),
            entry.parent_trace_id,
            entry.hash,
            entry.previous_hash,
        )

    @staticmethod
    def _is_sequence_collision(exc: Exception) -> bool:
        """Check if an exception represents a UNIQUE constraint violation on sequence."""
        if isinstance(exc, sqlite3.IntegrityError):
            msg = str(exc).lower()
            return "unique" in msg and "sequence" in msg
        try:
            import psycopg
            if isinstance(exc, psycopg.errors.UniqueViolation):
                msg = str(exc).lower()
                return "uq_audit_sequence" in msg or "sequence" in msg
        except ImportError:
            pass
        return False
