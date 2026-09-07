"""Archival — compressed, sealed, integrity-verified archives.

Glacial archives are the coldest storage tier: compressed JSON bundles
of object versions that are sealed (made immutable) and integrity-verified
via content hashing.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from scoped.storage.interface import StorageBackend
from scoped.types import Lifecycle, generate_id, now_utc


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class GlacialArchive:
    """A sealed, compressed archive of object version data."""

    id: str
    name: str
    description: str
    object_ids: list[str]           # IDs of objects included
    owner_id: str
    created_at: datetime
    sealed: bool
    sealed_at: datetime | None
    content_hash: str               # SHA-256 of compressed content
    compressed_size: int            # size in bytes
    original_size: int              # uncompressed size
    entry_count: int                # number of version entries archived
    lifecycle: Lifecycle


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    """A single object version stored in an archive."""

    object_id: str
    object_type: str
    version: int
    data: dict[str, Any]
    created_at: str
    created_by: str


# ---------------------------------------------------------------------------
# Row mapper
# ---------------------------------------------------------------------------

def archive_from_row(row: dict[str, Any]) -> GlacialArchive:
    return GlacialArchive(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        object_ids=json.loads(row["object_ids_json"]),
        owner_id=row["owner_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        sealed=bool(row["sealed"]),
        sealed_at=datetime.fromisoformat(row["sealed_at"]) if row.get("sealed_at") else None,
        content_hash=row["content_hash"],
        compressed_size=row["compressed_size"],
        original_size=row["original_size"],
        entry_count=row["entry_count"],
        lifecycle=Lifecycle[row["lifecycle"]],
    )


# ---------------------------------------------------------------------------
# ArchiveManager
# ---------------------------------------------------------------------------

class ArchiveManager:
    """Creates, seals, and verifies glacial archives."""

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_archive(
        self,
        *,
        object_ids: list[str],
        owner_id: str,
        name: str = "",
        description: str = "",
    ) -> GlacialArchive:
        """Create a glacial archive from object versions.

        Collects all versions of the specified objects, compresses them,
        and stores the archive. The archive is NOT sealed yet — call
        seal_archive() to make it immutable.
        """
        if not object_ids:
            raise ValueError("Cannot create archive with no object IDs")

        # Collect all version data for the specified objects
        entries: list[dict[str, Any]] = []
        for oid in object_ids:
            rows = self._backend.fetch_all(
                "SELECT ov.*, so.object_type FROM object_versions ov "
                "JOIN scoped_objects so ON ov.object_id = so.id "
                "WHERE ov.object_id = ? ORDER BY ov.version",
                (oid,),
            )
            for row in rows:
                entries.append({
                    "object_id": row["object_id"],
                    "object_type": row["object_type"],
                    "version": row["version"],
                    "data": json.loads(row["data_json"]),
                    "created_at": row["created_at"],
                    "created_by": row["created_by"],
                })

        # Serialize and compress
        payload = json.dumps(entries, sort_keys=True, default=str)
        original_size = len(payload.encode("utf-8"))
        compressed = gzip.compress(payload.encode("utf-8"))
        compressed_size = len(compressed)
        content_hash = hashlib.sha256(compressed).hexdigest()

        ts = now_utc()
        archive_id = generate_id()
        archive_name = name or f"archive-{archive_id[:8]}"

        self._backend.execute(
            "INSERT INTO glacial_archives "
            "(id, name, description, object_ids_json, owner_id, created_at, "
            "sealed, sealed_at, content_hash, compressed_data, "
            "compressed_size, original_size, entry_count, lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                archive_id, archive_name, description,
                json.dumps(object_ids), owner_id, ts.isoformat(),
                0, None, content_hash, compressed,
                compressed_size, original_size, len(entries),
                Lifecycle.ACTIVE.name,
            ),
        )

        return GlacialArchive(
            id=archive_id,
            name=archive_name,
            description=description,
            object_ids=object_ids,
            owner_id=owner_id,
            created_at=ts,
            sealed=False,
            sealed_at=None,
            content_hash=content_hash,
            compressed_size=compressed_size,
            original_size=original_size,
            entry_count=len(entries),
            lifecycle=Lifecycle.ACTIVE,
        )

    # ------------------------------------------------------------------
    # Seal
    # ------------------------------------------------------------------

    def seal_archive(self, archive_id: str) -> GlacialArchive:
        """Seal an archive, making it immutable.

        Once sealed, the archive cannot be modified or deleted.
        """
        archive = self.get_archive(archive_id)
        if archive is None:
            raise ValueError(f"Archive not found: {archive_id}")
        if archive.sealed:
            raise ValueError(f"Archive already sealed: {archive_id}")

        ts = now_utc()
        self._backend.execute(
            "UPDATE glacial_archives SET sealed = 1, sealed_at = ? WHERE id = ?",
            (ts.isoformat(), archive_id),
        )

        return GlacialArchive(
            id=archive.id,
            name=archive.name,
            description=archive.description,
            object_ids=archive.object_ids,
            owner_id=archive.owner_id,
            created_at=archive.created_at,
            sealed=True,
            sealed_at=ts,
            content_hash=archive.content_hash,
            compressed_size=archive.compressed_size,
            original_size=archive.original_size,
            entry_count=archive.entry_count,
            lifecycle=archive.lifecycle,
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_archive(self, archive_id: str) -> GlacialArchive | None:
        """Get archive metadata (without the compressed data)."""
        row = self._backend.fetch_one(
            "SELECT id, name, description, object_ids_json, owner_id, "
            "created_at, sealed, sealed_at, content_hash, "
            "compressed_size, original_size, entry_count, lifecycle "
            "FROM glacial_archives WHERE id = ?",
            (archive_id,),
        )
        return archive_from_row(row) if row else None

    def list_archives(
        self,
        *,
        owner_id: str | None = None,
        sealed_only: bool = False,
    ) -> list[GlacialArchive]:
        """List archives with optional filters."""
        clauses: list[str] = ["lifecycle != ?"]
        params: list[Any] = [Lifecycle.ARCHIVED.name]

        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(owner_id)

        if sealed_only:
            clauses.append("sealed = 1")

        where = " AND ".join(clauses)
        rows = self._backend.fetch_all(
            "SELECT id, name, description, object_ids_json, owner_id, "
            "created_at, sealed, sealed_at, content_hash, "
            "compressed_size, original_size, entry_count, lifecycle "
            f"FROM glacial_archives WHERE {where} ORDER BY created_at DESC",
            tuple(params),
        )
        return [archive_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Extract
    # ------------------------------------------------------------------

    def extract_archive(self, archive_id: str) -> list[ArchiveEntry]:
        """Decompress and return all entries from an archive."""
        row = self._backend.fetch_one(
            "SELECT compressed_data, content_hash FROM glacial_archives WHERE id = ?",
            (archive_id,),
        )
        if row is None:
            raise ValueError(f"Archive not found: {archive_id}")

        compressed = row["compressed_data"]
        content_hash = row["content_hash"]

        # Verify integrity
        actual_hash = hashlib.sha256(compressed).hexdigest()
        if actual_hash != content_hash:
            raise ValueError(
                f"Archive integrity check failed: expected {content_hash}, got {actual_hash}"
            )

        decompressed = gzip.decompress(compressed)
        entries_raw = json.loads(decompressed)

        return [
            ArchiveEntry(
                object_id=e["object_id"],
                object_type=e["object_type"],
                version=e["version"],
                data=e["data"],
                created_at=e["created_at"],
                created_by=e["created_by"],
            )
            for e in entries_raw
        ]

    def extract_object(self, archive_id: str, object_id: str) -> list[ArchiveEntry]:
        """Extract entries for a specific object from an archive."""
        all_entries = self.extract_archive(archive_id)
        return [e for e in all_entries if e.object_id == object_id]

    # ------------------------------------------------------------------
    # Verify
    # ------------------------------------------------------------------

    def verify_archive(self, archive_id: str) -> bool:
        """Verify the integrity of an archive's compressed data.

        Returns True if the content hash matches, False otherwise.
        """
        row = self._backend.fetch_one(
            "SELECT compressed_data, content_hash FROM glacial_archives WHERE id = ?",
            (archive_id,),
        )
        if row is None:
            raise ValueError(f"Archive not found: {archive_id}")

        actual_hash = hashlib.sha256(row["compressed_data"]).hexdigest()
        return actual_hash == row["content_hash"]

    # ------------------------------------------------------------------
    # Delete (unsealed only)
    # ------------------------------------------------------------------

    def delete_archive(self, archive_id: str) -> None:
        """Delete an unsealed archive. Sealed archives cannot be deleted."""
        archive = self.get_archive(archive_id)
        if archive is None:
            raise ValueError(f"Archive not found: {archive_id}")
        if archive.sealed:
            raise ValueError(f"Cannot delete sealed archive: {archive_id}")

        self._backend.execute(
            "DELETE FROM glacial_archives WHERE id = ?",
            (archive_id,),
        )
