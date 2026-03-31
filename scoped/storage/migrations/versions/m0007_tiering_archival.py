"""Migration 0007: Add storage tiering and archival tables.

Supports the Storage Tiering / Archival extension (A8).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scoped.storage.migrations.base import BaseMigration

if TYPE_CHECKING:
    from scoped.storage.interface import StorageBackend


_UP_SQL = """\
CREATE TABLE IF NOT EXISTS tier_assignments (
    id              TEXT PRIMARY KEY,
    object_id       TEXT NOT NULL,
    version         INTEGER NOT NULL,
    tier            TEXT NOT NULL DEFAULT 'HOT',
    assigned_at     TEXT NOT NULL,
    assigned_by     TEXT NOT NULL,
    previous_tier   TEXT,
    FOREIGN KEY (object_id) REFERENCES scoped_objects(id),
    UNIQUE(object_id, version)
);

CREATE INDEX IF NOT EXISTS idx_tier_object ON tier_assignments(object_id);
CREATE INDEX IF NOT EXISTS idx_tier_tier ON tier_assignments(tier);
CREATE INDEX IF NOT EXISTS idx_tier_assigned ON tier_assignments(assigned_at);

CREATE TABLE IF NOT EXISTS retention_policies (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    source_tier     TEXT NOT NULL,
    target_tier     TEXT NOT NULL,
    condition_type  TEXT NOT NULL,
    condition_value TEXT NOT NULL,
    object_type     TEXT,
    scope_id        TEXT,
    owner_id        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id),
    FOREIGN KEY (scope_id) REFERENCES scopes(id)
);

CREATE INDEX IF NOT EXISTS idx_retention_owner ON retention_policies(owner_id);
CREATE INDEX IF NOT EXISTS idx_retention_lifecycle ON retention_policies(lifecycle);

CREATE TABLE IF NOT EXISTS glacial_archives (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    object_ids_json TEXT NOT NULL DEFAULT '[]',
    owner_id        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    sealed          INTEGER NOT NULL DEFAULT 0,
    sealed_at       TEXT,
    content_hash    TEXT NOT NULL,
    compressed_data BLOB NOT NULL,
    compressed_size INTEGER NOT NULL,
    original_size   INTEGER NOT NULL,
    entry_count     INTEGER NOT NULL DEFAULT 0,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id)
);

CREATE INDEX IF NOT EXISTS idx_archive_owner ON glacial_archives(owner_id);
CREATE INDEX IF NOT EXISTS idx_archive_sealed ON glacial_archives(sealed);
"""


class AddTieringArchival(BaseMigration):
    @property
    def version(self) -> int:
        return 7

    @property
    def name(self) -> str:
        return "add_tiering_archival"

    def up(self, backend: StorageBackend) -> None:
        backend.execute_script(_UP_SQL)

    def down(self, backend: StorageBackend) -> None:
        backend.execute_script(
            "DROP TABLE IF EXISTS glacial_archives;\n"
            "DROP TABLE IF EXISTS retention_policies;\n"
            "DROP TABLE IF EXISTS tier_assignments;"
        )
