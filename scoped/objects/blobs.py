"""Blob management — versioned binary content linked to scoped objects.

Blobs are binary content (files, images, documents) that follow the same
isolation, versioning, and audit rules as JSON objects. A blob's metadata
is stored in the SQL database; the actual bytes live in a BlobBackend.

Key types:
  - ``BlobRef`` — a reference handle to a blob. Objects hold BlobRefs, not raw bytes.
  - ``BlobVersion`` — immutable record of a blob's content at a point in time.
  - ``BlobManager`` — CRUD for blobs with isolation and audit integration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from scoped.storage.blobs import BlobBackend
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BlobRef:
    """A reference handle to a stored blob.

    Components receive BlobRefs, not raw bytes.  Resolution (reading the
    actual content) happens through the BlobManager.
    """
    id: str
    filename: str
    content_type: str
    size_bytes: int
    content_hash: str
    owner_id: str
    created_at: datetime
    storage_path: str
    current_version: int = 1
    lifecycle: Lifecycle = Lifecycle.ACTIVE
    object_id: str | None = None
    metadata: dict[str, Any] | None = None

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "content_hash": self.content_hash,
            "owner_id": self.owner_id,
            "created_at": self.created_at.isoformat(),
            "storage_path": self.storage_path,
            "current_version": self.current_version,
            "lifecycle": self.lifecycle.name,
            "object_id": self.object_id,
        }


@dataclass(frozen=True, slots=True)
class BlobVersion:
    """Immutable snapshot of a blob at a specific version."""
    id: str
    blob_id: str
    version: int
    content_hash: str
    size_bytes: int
    storage_path: str
    created_at: datetime
    created_by: str
    change_reason: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "blob_id": self.blob_id,
            "version": self.version,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "storage_path": self.storage_path,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "change_reason": self.change_reason,
        }


# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------

def blob_ref_from_row(row: dict[str, Any]) -> BlobRef:
    return BlobRef(
        id=row["id"],
        filename=row["filename"],
        content_type=row["content_type"],
        size_bytes=row["size_bytes"],
        content_hash=row["content_hash"],
        owner_id=row["owner_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        storage_path=row["storage_path"],
        current_version=row["current_version"],
        lifecycle=Lifecycle[row["lifecycle"]],
        object_id=row.get("object_id"),
        metadata=json.loads(row["metadata_json"]) if row.get("metadata_json") else None,
    )


def blob_version_from_row(row: dict[str, Any]) -> BlobVersion:
    return BlobVersion(
        id=row["id"],
        blob_id=row["blob_id"],
        version=row["version"],
        content_hash=row["content_hash"],
        size_bytes=row["size_bytes"],
        storage_path=row["storage_path"],
        created_at=datetime.fromisoformat(row["created_at"]),
        created_by=row["created_by"],
        change_reason=row.get("change_reason", ""),
    )


# ---------------------------------------------------------------------------
# BlobManager
# ---------------------------------------------------------------------------

class BlobManager:
    """CRUD for blobs with isolation and audit integration.

    Usage::

        blob_backend = InMemoryBlobBackend()
        manager = BlobManager(db_backend, blob_backend)

        ref = manager.store(
            data=image_bytes,
            filename="photo.jpg",
            content_type="image/jpeg",
            owner_id=alice.id,
        )

        content = manager.read(ref.id, principal_id=alice.id)

        new_ref = manager.update(
            ref.id,
            data=new_bytes,
            principal_id=alice.id,
            change_reason="cropped",
        )
    """

    def __init__(
        self,
        backend: StorageBackend,
        blob_backend: BlobBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._blob_backend = blob_backend
        self._audit = audit_writer

    def store(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str,
        owner_id: str,
        object_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        change_reason: str = "created",
    ) -> BlobRef:
        """Store a new blob and return its reference."""
        ts = now_utc()
        blob_id = generate_id()
        content_hash = BlobBackend.compute_content_hash(data)
        size_bytes = len(data)

        # Store bytes in blob backend
        storage_path = self._blob_backend.store(blob_id, data)

        ref = BlobRef(
            id=blob_id,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            content_hash=content_hash,
            owner_id=owner_id,
            created_at=ts,
            storage_path=storage_path,
            object_id=object_id,
            metadata=metadata,
        )

        # Persist metadata
        self._backend.execute(
            "INSERT INTO blobs "
            "(id, filename, content_type, size_bytes, content_hash, "
            "owner_id, created_at, storage_path, current_version, "
            "lifecycle, object_id, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ref.id, ref.filename, ref.content_type,
                ref.size_bytes, ref.content_hash,
                ref.owner_id, ref.created_at.isoformat(),
                ref.storage_path, ref.current_version,
                ref.lifecycle.name, ref.object_id,
                json.dumps(metadata or {}),
            ),
        )

        # Create version 1
        self._create_version(ref, created_by=owner_id, change_reason=change_reason)

        if self._audit:
            self._audit.record(
                actor_id=owner_id,
                action=ActionType.BLOB_CREATE,
                target_type="Blob",
                target_id=blob_id,
                after_state=ref.snapshot(),
            )

        return ref

    def get(self, blob_id: str, *, principal_id: str) -> BlobRef | None:
        """Get a blob ref if the principal owns it."""
        ref = self._get_ref(blob_id)
        if ref is None:
            return None
        if ref.owner_id != principal_id:
            return None
        return ref

    def get_or_raise(self, blob_id: str, *, principal_id: str) -> BlobRef:
        """Get a blob ref or raise AccessDeniedError."""
        from scoped.exceptions import AccessDeniedError

        ref = self._get_ref(blob_id)
        if ref is None:
            raise AccessDeniedError(
                f"Blob {blob_id} not found",
                context={"blob_id": blob_id, "principal_id": principal_id},
            )
        if ref.owner_id != principal_id:
            raise AccessDeniedError(
                f"Access denied to blob {blob_id}",
                context={"blob_id": blob_id, "principal_id": principal_id},
            )
        return ref

    def read(self, blob_id: str, *, principal_id: str) -> bytes:
        """Read the binary content of a blob (isolation-enforced)."""
        ref = self.get_or_raise(blob_id, principal_id=principal_id)

        if self._audit:
            self._audit.record(
                actor_id=principal_id,
                action=ActionType.BLOB_READ,
                target_type="Blob",
                target_id=blob_id,
            )

        return self._blob_backend.retrieve(ref.storage_path)

    def update(
        self,
        blob_id: str,
        *,
        data: bytes,
        principal_id: str,
        change_reason: str = "",
    ) -> BlobRef:
        """Replace a blob's content, creating a new version."""
        ref = self.get_or_raise(blob_id, principal_id=principal_id)
        before = ref.snapshot()

        content_hash = BlobBackend.compute_content_hash(data)
        size_bytes = len(data)

        # Store new bytes
        new_path = self._blob_backend.store(f"{blob_id}_v{ref.current_version + 1}", data)

        ref.current_version += 1
        ref.content_hash = content_hash
        ref.size_bytes = size_bytes
        ref.storage_path = new_path

        self._backend.execute(
            "UPDATE blobs SET current_version = ?, content_hash = ?, "
            "size_bytes = ?, storage_path = ? WHERE id = ?",
            (ref.current_version, content_hash, size_bytes, new_path, blob_id),
        )

        self._create_version(ref, created_by=principal_id, change_reason=change_reason)

        if self._audit:
            self._audit.record(
                actor_id=principal_id,
                action=ActionType.BLOB_CREATE,
                target_type="Blob",
                target_id=blob_id,
                before_state=before,
                after_state=ref.snapshot(),
            )

        return ref

    def delete(self, blob_id: str, *, principal_id: str, reason: str = "") -> BlobRef:
        """Soft-delete a blob (archive its lifecycle)."""
        ref = self.get_or_raise(blob_id, principal_id=principal_id)
        before = ref.snapshot()

        ref.lifecycle = Lifecycle.ARCHIVED
        self._backend.execute(
            "UPDATE blobs SET lifecycle = ? WHERE id = ?",
            (Lifecycle.ARCHIVED.name, blob_id),
        )

        if self._audit:
            self._audit.record(
                actor_id=principal_id,
                action=ActionType.BLOB_DELETE,
                target_type="Blob",
                target_id=blob_id,
                before_state=before,
                after_state=ref.snapshot(),
                metadata={"reason": reason},
            )

        return ref

    def list_blobs(
        self,
        *,
        principal_id: str,
        object_id: str | None = None,
        content_type: str | None = None,
        active_only: bool = True,
    ) -> list[BlobRef]:
        """List blobs owned by a principal."""
        clauses = ["owner_id = ?"]
        params: list[Any] = [principal_id]

        if object_id is not None:
            clauses.append("object_id = ?")
            params.append(object_id)
        if content_type is not None:
            clauses.append("content_type = ?")
            params.append(content_type)
        if active_only:
            clauses.append("lifecycle = ?")
            params.append(Lifecycle.ACTIVE.name)

        where = " AND ".join(clauses)
        rows = self._backend.fetch_all(
            f"SELECT * FROM blobs WHERE {where} ORDER BY created_at DESC",
            tuple(params),
        )
        return [blob_ref_from_row(r) for r in rows]

    def get_version(self, blob_id: str, version: int) -> BlobVersion | None:
        """Get a specific version of a blob."""
        row = self._backend.fetch_one(
            "SELECT * FROM blob_versions WHERE blob_id = ? AND version = ?",
            (blob_id, version),
        )
        if row is None:
            return None
        return blob_version_from_row(row)

    def list_versions(self, blob_id: str, *, principal_id: str) -> list[BlobVersion]:
        """List all versions of a blob (isolation-enforced)."""
        self.get_or_raise(blob_id, principal_id=principal_id)
        rows = self._backend.fetch_all(
            "SELECT * FROM blob_versions WHERE blob_id = ? ORDER BY version ASC",
            (blob_id,),
        )
        return [blob_version_from_row(r) for r in rows]

    def link_to_object(self, blob_id: str, object_id: str) -> None:
        """Link a blob to a scoped object."""
        self._backend.execute(
            "UPDATE blobs SET object_id = ? WHERE id = ?",
            (object_id, blob_id),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_ref(self, blob_id: str) -> BlobRef | None:
        row = self._backend.fetch_one(
            "SELECT * FROM blobs WHERE id = ?", (blob_id,),
        )
        if row is None:
            return None
        return blob_ref_from_row(row)

    def _create_version(
        self,
        ref: BlobRef,
        *,
        created_by: str,
        change_reason: str = "",
    ) -> BlobVersion:
        ts = now_utc()
        ver_id = generate_id()
        ver = BlobVersion(
            id=ver_id,
            blob_id=ref.id,
            version=ref.current_version,
            content_hash=ref.content_hash,
            size_bytes=ref.size_bytes,
            storage_path=ref.storage_path,
            created_at=ts,
            created_by=created_by,
            change_reason=change_reason,
        )
        self._backend.execute(
            "INSERT INTO blob_versions "
            "(id, blob_id, version, content_hash, size_bytes, "
            "storage_path, created_at, created_by, change_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ver.id, ver.blob_id, ver.version,
                ver.content_hash, ver.size_bytes, ver.storage_path,
                ver.created_at.isoformat(), ver.created_by,
                ver.change_reason,
            ),
        )
        return ver
