"""Migration 0002: Add contracts and contract fields tables.

Supports the Contracts & Schema Validation extension (A2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scoped.storage.migrations.base import BaseMigration

if TYPE_CHECKING:
    from scoped.storage.interface import StorageBackend


_UP_SQL = """\
CREATE TABLE IF NOT EXISTS contracts (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    object_type     TEXT NOT NULL,
    owner_id        TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (owner_id) REFERENCES principals(id)
);

CREATE INDEX IF NOT EXISTS idx_contracts_object_type ON contracts(object_type);
CREATE INDEX IF NOT EXISTS idx_contracts_owner ON contracts(owner_id);
CREATE INDEX IF NOT EXISTS idx_contracts_lifecycle ON contracts(lifecycle);

CREATE TABLE IF NOT EXISTS contract_versions (
    id              TEXT PRIMARY KEY,
    contract_id     TEXT NOT NULL,
    version         INTEGER NOT NULL,
    fields_json     TEXT NOT NULL DEFAULT '[]',
    constraints_json TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    change_reason   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (contract_id) REFERENCES contracts(id),
    UNIQUE(contract_id, version)
);

CREATE INDEX IF NOT EXISTS idx_contract_versions ON contract_versions(contract_id);
"""


class AddContracts(BaseMigration):
    @property
    def version(self) -> int:
        return 2

    @property
    def name(self) -> str:
        return "add_contracts"

    def up(self, backend: StorageBackend) -> None:
        backend.execute_script(_UP_SQL)

    def down(self, backend: StorageBackend) -> None:
        backend.execute_script(
            "DROP TABLE IF EXISTS contract_versions;\n"
            "DROP TABLE IF EXISTS contracts;"
        )
