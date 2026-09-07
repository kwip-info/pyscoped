"""Migration 0016: Federation sender-sequence + receiver replay protection.

Adds two tables backing ``FederationProtocol``:

- ``federation_sequences`` — persisted sender sequence per connector so
  the counter survives process restarts.
- ``federation_seen_messages`` — receiver-side record of accepted
  messages keyed by (connector_id, sender_org_id, sequence), used to
  reject replays of previously-seen messages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scoped.storage.migrations.base import BaseMigration

if TYPE_CHECKING:
    from scoped.storage.interface import StorageBackend


_UP_SQL = """\
CREATE TABLE IF NOT EXISTS federation_sequences (
    connector_id    TEXT PRIMARY KEY,
    last_sequence   INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS federation_seen_messages (
    connector_id    TEXT NOT NULL,
    sender_org_id   TEXT NOT NULL,
    sequence        INTEGER NOT NULL,
    seen_at         TEXT NOT NULL,
    message_id      TEXT NOT NULL,
    PRIMARY KEY (connector_id, sender_org_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_fed_seen_seen_at
    ON federation_seen_messages (seen_at);
"""

_DOWN_SQL = """\
DROP INDEX IF EXISTS idx_fed_seen_seen_at;
DROP TABLE IF EXISTS federation_seen_messages;
DROP TABLE IF EXISTS federation_sequences;
"""


class FederationReplayProtection(BaseMigration):
    @property
    def version(self) -> int:
        return 16

    @property
    def name(self) -> str:
        return "federation_replay"

    def up(self, backend: StorageBackend) -> None:
        backend.execute_script(_UP_SQL)

    def down(self, backend: StorageBackend) -> None:
        backend.execute_script(_DOWN_SQL)
