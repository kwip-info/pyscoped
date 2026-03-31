"""Migration 0011: Add _sync_state table.

Tracks the sync agent's watermark position in the audit chain.
Single-row table (singleton pattern) — colocated with user data
so it participates in the same backup/restore.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scoped.storage.migrations.base import BaseMigration

if TYPE_CHECKING:
    from scoped.storage.interface import StorageBackend


_UP_SQL = """\
CREATE TABLE IF NOT EXISTS _sync_state (
    id              TEXT PRIMARY KEY DEFAULT 'singleton',
    last_sequence   INTEGER NOT NULL DEFAULT 0,
    last_hash       TEXT NOT NULL DEFAULT '',
    last_synced_at  TEXT,
    last_batch_id   TEXT,
    status          TEXT NOT NULL DEFAULT 'idle',
    error_message   TEXT,
    error_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""


class AddSyncState(BaseMigration):
    @property
    def version(self) -> int:
        return 11

    @property
    def name(self) -> str:
        return "sync_state"

    def up(self, backend: StorageBackend) -> None:
        backend.execute_script(_UP_SQL)

    def down(self, backend: StorageBackend) -> None:
        backend.execute("DROP TABLE IF EXISTS _sync_state")
