"""Migration 0005: Add search index tables.

Supports the Search / Indexing extension (A6).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scoped.storage.migrations.base import BaseMigration

if TYPE_CHECKING:
    from scoped.storage.interface import StorageBackend


_UP_SQL_COMMON = """\
CREATE TABLE IF NOT EXISTS search_index (
    id              TEXT PRIMARY KEY,
    object_id       TEXT NOT NULL,
    object_type     TEXT NOT NULL,
    owner_id        TEXT NOT NULL,
    field_name      TEXT NOT NULL,
    content         TEXT NOT NULL,
    scope_id        TEXT,
    indexed_at      TEXT NOT NULL,
    FOREIGN KEY (object_id) REFERENCES scoped_objects(id)
);

CREATE INDEX IF NOT EXISTS idx_search_object ON search_index(object_id);
CREATE INDEX IF NOT EXISTS idx_search_owner ON search_index(owner_id);
CREATE INDEX IF NOT EXISTS idx_search_type ON search_index(object_type);
CREATE INDEX IF NOT EXISTS idx_search_scope ON search_index(scope_id);
"""

_UP_SQL_SQLITE_FTS = """\
CREATE VIRTUAL TABLE IF NOT EXISTS search_index_fts USING fts5(
    content,
    content_rowid='rowid'
);
"""

_UP_SQL_POSTGRES_FTS = """\
ALTER TABLE search_index ADD COLUMN IF NOT EXISTS search_vector tsvector;
CREATE INDEX IF NOT EXISTS idx_search_fts ON search_index USING gin(search_vector);
"""


class AddSearchIndex(BaseMigration):
    @property
    def version(self) -> int:
        return 5

    @property
    def name(self) -> str:
        return "add_search_index"

    def up(self, backend: StorageBackend) -> None:
        backend.execute_script(_UP_SQL_COMMON)
        if backend.dialect == "postgres":
            backend.execute_script(_UP_SQL_POSTGRES_FTS)
        else:
            backend.execute_script(_UP_SQL_SQLITE_FTS)

    def down(self, backend: StorageBackend) -> None:
        if backend.dialect == "postgres":
            backend.execute_script(
                "DROP INDEX IF EXISTS idx_search_fts;\n"
                "DROP TABLE IF EXISTS search_index;"
            )
        else:
            backend.execute_script(
                "DROP TABLE IF EXISTS search_index_fts;\n"
                "DROP TABLE IF EXISTS search_index;"
            )
