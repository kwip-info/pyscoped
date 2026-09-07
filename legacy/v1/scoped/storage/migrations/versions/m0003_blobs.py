"""Migration 0003: Add blobs and blob versions tables.

Supports the Blob / Media Storage extension (A4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scoped.storage.migrations.base import BaseMigration

if TYPE_CHECKING:
    from scoped.storage.interface import StorageBackend


_UP_SQL = """\
CREATE TABLE IF NOT EXISTS blobs (
    id              TEXT PRIMARY KEY,
    filename        TEXT NOT NULL,
    content_type    TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    content_hash    TEXT NOT NULL,
    owner_id        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    storage_path    TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    object_id       TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (owner_id) REFERENCES principals(id),
    FOREIGN KEY (object_id) REFERENCES scoped_objects(id)
);

CREATE INDEX IF NOT EXISTS idx_blobs_owner ON blobs(owner_id);
CREATE INDEX IF NOT EXISTS idx_blobs_object ON blobs(object_id);
CREATE INDEX IF NOT EXISTS idx_blobs_content_type ON blobs(content_type);
CREATE INDEX IF NOT EXISTS idx_blobs_lifecycle ON blobs(lifecycle);

CREATE TABLE IF NOT EXISTS blob_versions (
    id              TEXT PRIMARY KEY,
    blob_id         TEXT NOT NULL,
    version         INTEGER NOT NULL,
    content_hash    TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    storage_path    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    change_reason   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (blob_id) REFERENCES blobs(id),
    UNIQUE(blob_id, version)
);

CREATE INDEX IF NOT EXISTS idx_blob_versions ON blob_versions(blob_id);
"""


class AddBlobs(BaseMigration):
    @property
    def version(self) -> int:
        return 3

    @property
    def name(self) -> str:
        return "add_blobs"

    def up(self, backend: StorageBackend) -> None:
        backend.execute_script(_UP_SQL)

    def down(self, backend: StorageBackend) -> None:
        backend.execute_script(
            "DROP TABLE IF EXISTS blob_versions;\n"
            "DROP TABLE IF EXISTS blobs;"
        )
