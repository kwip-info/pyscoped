"""Migration registry — tracks which migrations have been applied."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from scoped.types import now_utc

if TYPE_CHECKING:
    from scoped.storage.interface import StorageBackend


MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS scoped_migrations (
    version         INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    applied_at      TEXT NOT NULL,
    checksum        TEXT NOT NULL DEFAULT ''
);
"""


@dataclass(frozen=True, slots=True)
class MigrationRecord:
    """Record of an applied migration."""

    version: int
    name: str
    applied_at: datetime
    checksum: str = ""

    def snapshot(self) -> dict:
        return {
            "version": self.version,
            "name": self.name,
            "applied_at": self.applied_at.isoformat(),
            "checksum": self.checksum,
        }


class MigrationRegistry:
    """Tracks applied migrations in the database.

    The scoped_migrations table is bootstrap-level — it's created before
    any other schema and is the source of truth for what version the
    database is at.
    """

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def ensure_table(self) -> None:
        """Create the migrations tracking table if it doesn't exist."""
        self._backend.execute(MIGRATIONS_TABLE_SQL)

    def get_applied_versions(self) -> list[int]:
        """Return sorted list of applied migration version numbers."""
        rows = self._backend.fetch_all(
            "SELECT version FROM scoped_migrations ORDER BY version"
        )
        return [row["version"] for row in rows]

    def get_applied_migrations(self) -> list[MigrationRecord]:
        """Return all applied migration records, ordered by version."""
        rows = self._backend.fetch_all(
            "SELECT version, name, applied_at, checksum "
            "FROM scoped_migrations ORDER BY version"
        )
        return [
            MigrationRecord(
                version=row["version"],
                name=row["name"],
                applied_at=datetime.fromisoformat(row["applied_at"]),
                checksum=row.get("checksum", ""),
            )
            for row in rows
        ]

    def record_applied(self, version: int, name: str, checksum: str = "") -> MigrationRecord:
        """Record that a migration has been applied."""
        ts = now_utc()
        self._backend.execute(
            "INSERT INTO scoped_migrations (version, name, applied_at, checksum) "
            "VALUES (?, ?, ?, ?)",
            (version, name, ts.isoformat(), checksum),
        )
        return MigrationRecord(version=version, name=name, applied_at=ts, checksum=checksum)

    def record_rolled_back(self, version: int) -> None:
        """Remove the record of a migration (it has been rolled back)."""
        self._backend.execute(
            "DELETE FROM scoped_migrations WHERE version = ?",
            (version,),
        )

    def get_current_version(self) -> int:
        """Return the highest applied migration version, or 0 if none."""
        row = self._backend.fetch_one(
            "SELECT MAX(version) as max_version FROM scoped_migrations"
        )
        if row is None or row["max_version"] is None:
            return 0
        return row["max_version"]

    def is_applied(self, version: int) -> bool:
        """Check if a specific migration version has been applied."""
        row = self._backend.fetch_one(
            "SELECT version FROM scoped_migrations WHERE version = ?",
            (version,),
        )
        return row is not None
