"""SQLite-backed registry store.

Bridges the in-memory Registry with the SQLite storage backend.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from scoped.registry.base import RegistryEntry
from scoped.registry.store import RegistryStore
from scoped.storage._query import compile_for, dialect_insert
from scoped.storage._schema import registry_entries
from scoped.storage.interface import StorageBackend


class SQLiteRegistryStore(RegistryStore):
    """Persist registry entries to SQLite via a StorageBackend."""

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def save_entry(self, entry: RegistryEntry) -> None:
        snapshot = entry.snapshot()
        _cols = [
            "urn", "kind", "namespace", "name", "lifecycle",
            "registered_at", "registered_by", "entry_version",
            "previous_entry_id", "metadata_json", "tags_json",
        ]
        stmt = dialect_insert(registry_entries, self._backend.dialect).values(
            id=snapshot["id"],
            urn=snapshot["urn"],
            kind=snapshot["kind"],
            namespace=snapshot["namespace"],
            name=entry.urn.name,
            lifecycle=snapshot["lifecycle"],
            registered_at=snapshot["registered_at"],
            registered_by=snapshot["registered_by"],
            entry_version=snapshot["entry_version"],
            previous_entry_id=entry.previous_entry_id,
            metadata_json=json.dumps(snapshot["metadata"]),
            tags_json=json.dumps(snapshot["tags"]),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={col: stmt.excluded[col] for col in _cols},
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

    def save_all(self, entries: list[RegistryEntry]) -> None:
        tx = self._backend.transaction()
        try:
            for entry in entries:
                snapshot = entry.snapshot()
                _cols = [
                    "urn", "kind", "namespace", "name", "lifecycle",
                    "registered_at", "registered_by", "entry_version",
                    "previous_entry_id", "metadata_json", "tags_json",
                ]
                stmt = dialect_insert(registry_entries, self._backend.dialect).values(
                    id=snapshot["id"],
                    urn=snapshot["urn"],
                    kind=snapshot["kind"],
                    namespace=snapshot["namespace"],
                    name=entry.urn.name,
                    lifecycle=snapshot["lifecycle"],
                    registered_at=snapshot["registered_at"],
                    registered_by=snapshot["registered_by"],
                    entry_version=snapshot["entry_version"],
                    previous_entry_id=entry.previous_entry_id,
                    metadata_json=json.dumps(snapshot["metadata"]),
                    tags_json=json.dumps(snapshot["tags"]),
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_={col: stmt.excluded[col] for col in _cols},
                )
                sql, params = compile_for(stmt, self._backend.dialect)
                tx.execute(sql, params)
            tx.commit()
        except Exception:
            tx.rollback()
            raise

    def load_all(self) -> list[dict[str, Any]]:
        stmt = sa.select(registry_entries)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        results = []
        for row in rows:
            results.append({
                "id": row["id"],
                "urn": row["urn"],
                "kind": row["kind"],
                "namespace": row["namespace"],
                "name": row["name"],
                "lifecycle": row["lifecycle"],
                "registered_at": row["registered_at"],
                "registered_by": row["registered_by"],
                "entry_version": row["entry_version"],
                "previous_entry_id": row["previous_entry_id"],
                "metadata": json.loads(row["metadata_json"]),
                "tags": json.loads(row["tags_json"]),
            })
        return results

    def delete_entry(self, entry_id: str) -> None:
        stmt = sa.delete(registry_entries).where(registry_entries.c.id == entry_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

    def clear(self) -> None:
        stmt = sa.delete(registry_entries)
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
