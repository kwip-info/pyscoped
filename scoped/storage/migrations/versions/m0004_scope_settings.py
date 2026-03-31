"""Migration 0004: Add scope_settings table.

Supports the Configuration Hierarchy extension (A5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scoped.storage.migrations.base import BaseMigration

if TYPE_CHECKING:
    from scoped.storage.interface import StorageBackend


_UP_SQL = """\
CREATE TABLE IF NOT EXISTS scope_settings (
    id              TEXT PRIMARY KEY,
    scope_id        TEXT NOT NULL,
    key             TEXT NOT NULL,
    value_json      TEXT NOT NULL DEFAULT 'null',
    description     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    updated_at      TEXT,
    updated_by      TEXT,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (scope_id) REFERENCES scopes(id),
    UNIQUE(scope_id, key)
);

CREATE INDEX IF NOT EXISTS idx_scope_settings_scope ON scope_settings(scope_id);
CREATE INDEX IF NOT EXISTS idx_scope_settings_key ON scope_settings(key);
"""


class AddScopeSettings(BaseMigration):
    @property
    def version(self) -> int:
        return 4

    @property
    def name(self) -> str:
        return "add_scope_settings"

    def up(self, backend: StorageBackend) -> None:
        backend.execute_script(_UP_SQL)

    def down(self, backend: StorageBackend) -> None:
        backend.execute_script("DROP TABLE IF EXISTS scope_settings;")
