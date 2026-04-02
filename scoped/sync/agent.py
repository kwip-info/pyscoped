"""Background sync agent — pushes audit metadata to the management plane.

The agent runs as a daemon thread, reading from the local audit trail
and pushing batches to the management plane API. State is persisted in
the ``_sync_state`` table so the agent can resume after restarts.

Usage::

    from scoped.sync.agent import SyncAgent
    from scoped.sync.config import SyncConfig

    agent = SyncAgent(
        backend=backend,
        api_key="psc_live_...",
        config=SyncConfig(interval_seconds=30),
    )
    agent.start()
    # ... later ...
    agent.pause()
    agent.resume()
    agent.stop()

The agent is typically not used directly — it's wired into
``ScopedClient`` via ``client.start_sync()``.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from scoped.exceptions import SyncError, SyncTransportError
from scoped.storage._query import compile_for, dialect_insert
from scoped.storage._schema import (
    _sync_state,
    audit_trail,
    principals,
    scoped_objects,
    scopes,
)
from scoped.storage.interface import StorageBackend
from scoped.sync.config import SyncConfig
from scoped.sync.models import (
    ResourceCounts,
    SyncBatch,
    SyncBatchAck,
    SyncEntryMetadata,
    SyncStateSnapshot,
    SyncStatus,
    SyncVerifyRequest,
    SyncVerifyResponse,
)
from scoped.sync.transport import ManagementPlaneClient
from scoped.types import generate_id, now_utc


class SyncAgent:
    """Background sync agent that pushes audit metadata to the management plane.

    Lifecycle: ``idle -> syncing <-> paused -> stopped``

    Thread-safe — all public methods can be called from any thread.

    Args:
        backend: The storage backend to read audit entries from.
        api_key: Management plane API key.
        config: Sync configuration (interval, batch size, retries).
        transport: Optional pre-built transport client (for testing).
    """

    def __init__(
        self,
        *,
        backend: StorageBackend,
        api_key: str,
        config: SyncConfig | None = None,
        transport: ManagementPlaneClient | None = None,
    ) -> None:
        self._backend = backend
        self._api_key = api_key
        self._config = config or SyncConfig()
        self._transport = transport or ManagementPlaneClient(
            api_key=api_key,
            base_url=self._config.base_url,
            timeout=self._config.request_timeout_seconds,
        )
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # not paused by default
        self._lock = threading.Lock()

    # -- Public lifecycle --------------------------------------------------

    def start(self) -> None:
        """Start the background sync loop.

        Creates the ``_sync_state`` singleton row if it doesn't exist,
        then launches a daemon thread that runs sync cycles.

        Raises:
            SyncError: If the agent is already running.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise SyncError("Sync agent is already running.")

            self._ensure_sync_state()
            self._update_status(SyncStatus.SYNCING)

            self._stop_event.clear()
            self._pause_event.set()
            self._thread = threading.Thread(
                target=self._run, name="pyscoped-sync", daemon=True
            )
            self._thread.start()

    def pause(self) -> None:
        """Temporarily pause sync. State is preserved."""
        self._pause_event.clear()
        self._update_status(SyncStatus.PAUSED)

    def resume(self) -> None:
        """Resume sync from where it was paused."""
        self._update_status(SyncStatus.SYNCING)
        self._pause_event.set()

    def stop(self) -> None:
        """Stop sync and clean up the thread."""
        self._stop_event.set()
        self._pause_event.set()  # unblock if paused
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        self._update_status(SyncStatus.STOPPED)

    def status(self) -> SyncStateSnapshot:
        """Return current sync state from the ``_sync_state`` table."""
        stmt = sa.select(_sync_state).where(_sync_state.c.id == "singleton")
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            return SyncStateSnapshot()
        return SyncStateSnapshot(
            last_sequence=row["last_sequence"],
            last_hash=row["last_hash"],
            last_synced_at=(
                datetime.fromisoformat(row["last_synced_at"])
                if row.get("last_synced_at")
                else None
            ),
            last_batch_id=row.get("last_batch_id"),
            status=SyncStatus(row["status"]),
            error_message=row.get("error_message"),
            error_count=row["error_count"],
        )

    def verify(self) -> SyncVerifyResponse:
        """Verify local audit chain matches what the server received."""
        state = self.status()
        from scoped.audit.query import AuditQuery

        query = AuditQuery(self._backend)
        chain = query.verify_chain()

        req = SyncVerifyRequest(
            from_sequence=1,
            to_sequence=state.last_sequence or None,
            local_chain_hash=state.last_hash,
            local_entry_count=chain.entries_checked,
        )
        return self._transport.verify_sync(req)

    # -- Internal sync loop ------------------------------------------------

    def _run(self) -> None:
        """Main sync loop (runs in background thread)."""
        while not self._stop_event.is_set():
            # Block if paused
            self._pause_event.wait()
            if self._stop_event.is_set():
                break

            try:
                self._sync_cycle()
            except Exception as exc:
                self._handle_error(exc)

            self._stop_event.wait(timeout=self._config.interval_seconds)

    def _sync_cycle(self) -> None:
        """Execute one sync cycle: read -> package -> push -> update watermark."""
        state = self.status()

        # 1. Query new audit entries (no before_state/after_state)
        entries = self._query_new_entries(state.last_sequence)
        if not entries:
            return

        # 2. Count active resources
        counts = self._count_resources()

        # 3. Build and sign the batch
        batch = self._build_batch(entries, counts)

        # 4. Push to management plane
        ack = self._transport.push_batch(batch)

        # 5. Update watermark on success
        if ack.accepted:
            self._update_watermark(
                last_sequence=batch.last_sequence,
                last_hash=batch.chain_hash,
                batch_id=batch.batch_id,
            )

    def _query_new_entries(self, after_sequence: int) -> list[SyncEntryMetadata]:
        """Query audit_trail for entries after the watermark.

        Deliberately excludes before_state and after_state — data never
        leaves customer infrastructure.
        """
        stmt = (
            sa.select(
                audit_trail.c.id,
                audit_trail.c.sequence,
                audit_trail.c.actor_id,
                audit_trail.c.action,
                audit_trail.c.target_type,
                audit_trail.c.target_id,
                audit_trail.c.timestamp,
                audit_trail.c.hash,
                audit_trail.c.previous_hash,
                audit_trail.c.scope_id,
                audit_trail.c.parent_trace_id,
                audit_trail.c.metadata_json,
            )
            .where(audit_trail.c.sequence > after_sequence)
            .order_by(audit_trail.c.sequence.asc())
            .limit(self._config.batch_size)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [
            SyncEntryMetadata(
                id=r["id"],
                sequence=r["sequence"],
                actor_id=r["actor_id"],
                action=r["action"],
                target_type=r["target_type"],
                target_id=r["target_id"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                hash=r["hash"],
                previous_hash=r.get("previous_hash", ""),
                scope_id=r.get("scope_id"),
                parent_trace_id=r.get("parent_trace_id"),
                metadata=json.loads(r.get("metadata_json", "{}")),
            )
            for r in rows
        ]

    def _count_resources(self) -> ResourceCounts:
        """Count active objects, principals, and scopes."""
        obj_stmt = (
            sa.select(sa.func.count().label("cnt"))
            .select_from(scoped_objects)
            .where(scoped_objects.c.lifecycle == "ACTIVE")
        )
        sql_o, params_o = compile_for(obj_stmt, self._backend.dialect)
        obj_row = self._backend.fetch_one(sql_o, params_o)

        prin_stmt = (
            sa.select(sa.func.count().label("cnt"))
            .select_from(principals)
            .where(principals.c.lifecycle == "ACTIVE")
        )
        sql_p, params_p = compile_for(prin_stmt, self._backend.dialect)
        prin_row = self._backend.fetch_one(sql_p, params_p)

        scope_stmt = (
            sa.select(sa.func.count().label("cnt"))
            .select_from(scopes)
            .where(scopes.c.lifecycle == "ACTIVE")
        )
        sql_s, params_s = compile_for(scope_stmt, self._backend.dialect)
        scope_row = self._backend.fetch_one(sql_s, params_s)

        return ResourceCounts(
            active_objects=obj_row["cnt"] if obj_row else 0,
            active_principals=prin_row["cnt"] if prin_row else 0,
            active_scopes=scope_row["cnt"] if scope_row else 0,
            timestamp=now_utc(),
        )

    def _build_batch(
        self, entries: list[SyncEntryMetadata], counts: ResourceCounts
    ) -> SyncBatch:
        """Package entries into a signed SyncBatch."""
        from scoped import __version__

        content_hash = self._transport.compute_content_hash(entries)
        chain_hash = entries[-1].hash if entries else ""

        batch_json_for_signing = json.dumps(
            {
                "entries": [e.model_dump(mode="json") for e in entries],
                "content_hash": content_hash,
                "chain_hash": chain_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        signature = self._transport.sign_payload(batch_json_for_signing)

        return SyncBatch(
            batch_id=generate_id(),
            sdk_version=__version__,
            entries=entries,
            resource_counts=counts,
            first_sequence=entries[0].sequence,
            last_sequence=entries[-1].sequence,
            chain_hash=chain_hash,
            content_hash=content_hash,
            signature=signature,
            created_at=now_utc(),
        )

    # -- State management --------------------------------------------------

    def _ensure_sync_state(self) -> None:
        """Create the singleton _sync_state row if it doesn't exist."""
        ts = now_utc().isoformat()
        stmt = dialect_insert(_sync_state, self._backend.dialect).values(
            id="singleton",
            last_sequence=0,
            last_hash="",
            status="idle",
            error_count=0,
            created_at=ts,
            updated_at=ts,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

    def _update_watermark(
        self, *, last_sequence: int, last_hash: str, batch_id: str
    ) -> None:
        """Update _sync_state after a successful batch push."""
        ts = now_utc().isoformat()
        stmt = (
            sa.update(_sync_state)
            .where(_sync_state.c.id == "singleton")
            .values(
                last_sequence=last_sequence,
                last_hash=last_hash,
                last_synced_at=ts,
                last_batch_id=batch_id,
                status="syncing",
                error_message=None,
                error_count=0,
                updated_at=ts,
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

    def _update_status(self, status: SyncStatus) -> None:
        """Update the status field in _sync_state."""
        ts = now_utc().isoformat()
        stmt = (
            sa.update(_sync_state)
            .where(_sync_state.c.id == "singleton")
            .values(status=status.value, updated_at=ts)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

    def _handle_error(self, exc: Exception) -> None:
        """Record error state and compute backoff delay."""
        state = self.status()
        error_count = state.error_count + 1
        ts = now_utc().isoformat()

        stmt = (
            sa.update(_sync_state)
            .where(_sync_state.c.id == "singleton")
            .values(
                status="error",
                error_message=str(exc)[:500],
                error_count=error_count,
                updated_at=ts,
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        # Exponential backoff with jitter
        delay = min(
            self._config.retry_base_delay_seconds * (2 ** error_count)
            + random.uniform(0, 1),
            self._config.retry_max_delay_seconds,
        )
        self._stop_event.wait(timeout=delay)
