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

from scoped.exceptions import SyncError, SyncTransportError
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

    Lifecycle: ``idle → syncing ⇄ paused → stopped``

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
        row = self._backend.fetch_one(
            "SELECT * FROM _sync_state WHERE id = 'singleton'", ()
        )
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
        """Execute one sync cycle: read → package → push → update watermark."""
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
        rows = self._backend.fetch_all(
            "SELECT id, sequence, actor_id, action, target_type, target_id, "
            "timestamp, hash, previous_hash, scope_id, parent_trace_id, metadata_json "
            "FROM audit_trail "
            "WHERE sequence > ? "
            "ORDER BY sequence ASC "
            "LIMIT ?",
            (after_sequence, self._config.batch_size),
        )
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
        obj_row = self._backend.fetch_one(
            "SELECT COUNT(*) as cnt FROM scoped_objects WHERE lifecycle = 'ACTIVE'", ()
        )
        prin_row = self._backend.fetch_one(
            "SELECT COUNT(*) as cnt FROM principals WHERE lifecycle = 'ACTIVE'", ()
        )
        scope_row = self._backend.fetch_one(
            "SELECT COUNT(*) as cnt FROM scopes WHERE lifecycle = 'ACTIVE'", ()
        )
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
        self._backend.execute(
            "INSERT OR IGNORE INTO _sync_state "
            "(id, last_sequence, last_hash, status, error_count, created_at, updated_at) "
            "VALUES ('singleton', 0, '', 'idle', 0, ?, ?)",
            (ts, ts),
        )

    def _update_watermark(
        self, *, last_sequence: int, last_hash: str, batch_id: str
    ) -> None:
        """Update _sync_state after a successful batch push."""
        ts = now_utc().isoformat()
        self._backend.execute(
            "UPDATE _sync_state "
            "SET last_sequence = ?, last_hash = ?, last_synced_at = ?, "
            "last_batch_id = ?, status = 'syncing', error_message = NULL, "
            "error_count = 0, updated_at = ? "
            "WHERE id = 'singleton'",
            (last_sequence, last_hash, ts, batch_id, ts),
        )

    def _update_status(self, status: SyncStatus) -> None:
        """Update the status field in _sync_state."""
        ts = now_utc().isoformat()
        self._backend.execute(
            "UPDATE _sync_state SET status = ?, updated_at = ? WHERE id = 'singleton'",
            (status.value, ts),
        )

    def _handle_error(self, exc: Exception) -> None:
        """Record error state and compute backoff delay."""
        state = self.status()
        error_count = state.error_count + 1
        ts = now_utc().isoformat()

        self._backend.execute(
            "UPDATE _sync_state "
            "SET status = 'error', error_message = ?, "
            "error_count = ?, updated_at = ? "
            "WHERE id = 'singleton'",
            (str(exc)[:500], error_count, ts),
        )

        # Exponential backoff with jitter
        delay = min(
            self._config.retry_base_delay_seconds * (2 ** error_count)
            + random.uniform(0, 1),
            self._config.retry_max_delay_seconds,
        )
        self._stop_event.wait(timeout=delay)
