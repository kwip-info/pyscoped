"""Migration 0006: Add general templates tables.

Supports the General Templates extension (A7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scoped.storage.migrations.base import BaseMigration

if TYPE_CHECKING:
    from scoped.storage.interface import StorageBackend


_UP_SQL = """\
CREATE TABLE IF NOT EXISTS templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    template_type   TEXT NOT NULL,
    owner_id        TEXT NOT NULL,
    schema_json     TEXT NOT NULL DEFAULT '{}',
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    scope_id        TEXT,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id),
    FOREIGN KEY (scope_id) REFERENCES scopes(id)
);

CREATE INDEX IF NOT EXISTS idx_templates_owner ON templates(owner_id);
CREATE INDEX IF NOT EXISTS idx_templates_type ON templates(template_type);
CREATE INDEX IF NOT EXISTS idx_templates_scope ON templates(scope_id);
CREATE INDEX IF NOT EXISTS idx_templates_lifecycle ON templates(lifecycle);

CREATE TABLE IF NOT EXISTS template_versions (
    id              TEXT PRIMARY KEY,
    template_id     TEXT NOT NULL,
    version         INTEGER NOT NULL,
    schema_json     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    change_reason   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (template_id) REFERENCES templates(id),
    UNIQUE(template_id, version)
);

CREATE INDEX IF NOT EXISTS idx_template_versions ON template_versions(template_id);
"""


class AddTemplates(BaseMigration):
    @property
    def version(self) -> int:
        return 6

    @property
    def name(self) -> str:
        return "add_templates"

    def up(self, backend: StorageBackend) -> None:
        backend.execute_script(_UP_SQL)

    def down(self, backend: StorageBackend) -> None:
        backend.execute_script(
            "DROP TABLE IF EXISTS template_versions;\n"
            "DROP TABLE IF EXISTS templates;"
        )
