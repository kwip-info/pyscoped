"""SQLite-backed registry store.

Bridges the in-memory Registry with the SQLite storage backend.
"""

from __future__ import annotations

import json
from typing import Any

from scoped.registry.base import RegistryEntry
from scoped.registry.store import RegistryStore
from scoped.storage.interface import StorageBackend


class SQLiteRegistryStore(RegistryStore):
    """Persist registry entries to SQLite via a StorageBackend."""

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def save_entry(self, entry: RegistryEntry) -> None:
        snapshot = entry.snapshot()
        self._backend.execute(
            """
            INSERT INTO registry_entries
                (id, urn, kind, namespace, name, lifecycle, registered_at,
                 registered_by, entry_version, previous_entry_id, metadata_json, tags_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                urn = excluded.urn,
                kind = excluded.kind,
                namespace = excluded.namespace,
                name = excluded.name,
                lifecycle = excluded.lifecycle,
                registered_at = excluded.registered_at,
                registered_by = excluded.registered_by,
                entry_version = excluded.entry_version,
                previous_entry_id = excluded.previous_entry_id,
                metadata_json = excluded.metadata_json,
                tags_json = excluded.tags_json
            """,
            (
                snapshot["id"],
                snapshot["urn"],
                snapshot["kind"],
                snapshot["namespace"],
                entry.urn.name,
                snapshot["lifecycle"],
                snapshot["registered_at"],
                snapshot["registered_by"],
                snapshot["entry_version"],
                entry.previous_entry_id,
                json.dumps(snapshot["metadata"]),
                json.dumps(snapshot["tags"]),
            ),
        )

    def save_all(self, entries: list[RegistryEntry]) -> None:
        tx = self._backend.transaction()
        try:
            for entry in entries:
                snapshot = entry.snapshot()
                tx.execute(
                    """
                    INSERT OR REPLACE INTO registry_entries
                        (id, urn, kind, namespace, name, lifecycle, registered_at,
                         registered_by, entry_version, previous_entry_id, metadata_json, tags_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot["id"],
                        snapshot["urn"],
                        snapshot["kind"],
                        snapshot["namespace"],
                        entry.urn.name,
                        snapshot["lifecycle"],
                        snapshot["registered_at"],
                        snapshot["registered_by"],
                        snapshot["entry_version"],
                        entry.previous_entry_id,
                        json.dumps(snapshot["metadata"]),
                        json.dumps(snapshot["tags"]),
                    ),
                )
            tx.commit()
        except Exception:
            tx.rollback()
            raise

    def load_all(self) -> list[dict[str, Any]]:
        rows = self._backend.fetch_all("SELECT * FROM registry_entries")
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
        self._backend.execute(
            "DELETE FROM registry_entries WHERE id = ?",
            (entry_id,),
        )

    def clear(self) -> None:
        self._backend.execute("DELETE FROM registry_entries")
