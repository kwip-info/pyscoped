"""ScopedManager — isolation-enforcing object manager.

Every query goes through here. No raw access to objects bypasses isolation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from scoped.exceptions import (
    AccessDeniedError,
    IsolationViolationError,
)
from scoped.objects.isolation import can_access
from scoped.objects.models import (
    ObjectVersion,
    ScopedObject,
    Tombstone,
    compute_checksum,
)
from scoped.objects.versioning import diff_versions
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc


class ScopedManager:
    """Isolation-enforcing CRUD manager for scoped objects.

    All reads are filtered to the acting principal's visibility.
    All writes create new versions (no in-place updates).
    All operations are traced via the audit writer (when provided).
    """

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        object_type: str,
        owner_id: str,
        data: dict[str, Any],
        registry_entry_id: str | None = None,
        change_reason: str = "created",
    ) -> tuple[ScopedObject, ObjectVersion]:
        """Create a new scoped object with its first version.

        Returns (object, version_1).
        """
        ts = now_utc()
        obj_id = generate_id()
        ver_id = generate_id()
        checksum = compute_checksum(data)

        obj = ScopedObject(
            id=obj_id,
            object_type=object_type,
            owner_id=owner_id,
            current_version=1,
            created_at=ts,
            lifecycle=Lifecycle.ACTIVE,
            registry_entry_id=registry_entry_id,
        )
        ver = ObjectVersion(
            id=ver_id,
            object_id=obj_id,
            version=1,
            data=data,
            created_at=ts,
            created_by=owner_id,
            change_reason=change_reason,
            checksum=checksum,
        )

        self._persist_object(obj)
        self._persist_version(ver)

        if self._audit:
            self._audit.record(
                actor_id=owner_id,
                action=ActionType.CREATE,
                target_type=object_type,
                target_id=obj_id,
                after_state=data,
            )

        return obj, ver

    # ------------------------------------------------------------------
    # Read (isolation-enforced)
    # ------------------------------------------------------------------

    def get(self, object_id: str, *, principal_id: str) -> ScopedObject | None:
        """Get an object by ID if the principal can see it."""
        obj = self._load_object(object_id)
        if obj is None:
            return None
        if not can_access(obj.owner_id, principal_id):
            return None
        return obj

    def get_or_raise(self, object_id: str, *, principal_id: str) -> ScopedObject:
        """Get an object by ID or raise AccessDeniedError."""
        obj = self._load_object(object_id)
        if obj is None:
            raise AccessDeniedError(
                f"Object {object_id} not found or access denied",
                context={"object_id": object_id, "principal_id": principal_id},
            )
        if not can_access(obj.owner_id, principal_id):
            raise AccessDeniedError(
                f"Principal {principal_id} cannot access object {object_id}",
                context={"object_id": object_id, "principal_id": principal_id},
            )
        return obj

    # Columns that are safe to ORDER BY
    _OBJECT_ORDER_COLUMNS = {"created_at", "object_type"}

    def list_objects(
        self,
        *,
        principal_id: str,
        object_type: str | None = None,
        include_tombstoned: bool = False,
        order_by: str = "created_at",
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScopedObject]:
        """List objects visible to a principal (owner-only at this layer).

        Args:
            order_by: Column to sort by. Prefix with ``-`` for descending.
                      Allowed: ``created_at``, ``object_type``. Default: ``created_at``.
        """
        clauses = ["owner_id = ?"]
        params: list[Any] = [principal_id]

        if object_type is not None:
            clauses.append("object_type = ?")
            params.append(object_type)

        if not include_tombstoned:
            clauses.append("lifecycle != ?")
            params.append(Lifecycle.ARCHIVED.name)

        where = " AND ".join(clauses)

        desc = order_by.startswith("-")
        col = order_by.lstrip("-")
        if col not in self._OBJECT_ORDER_COLUMNS:
            col = "created_at"
        direction = "DESC" if desc else "ASC"

        sql = (
            f"SELECT * FROM scoped_objects WHERE {where} "
            f"ORDER BY {col} {direction} LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        rows = self._backend.fetch_all(sql, tuple(params))
        return [self._row_to_object(r) for r in rows]

    def count(
        self,
        *,
        principal_id: str,
        object_type: str | None = None,
        include_tombstoned: bool = False,
    ) -> int:
        """Count objects visible to a principal."""
        clauses = ["owner_id = ?"]
        params: list[Any] = [principal_id]

        if object_type is not None:
            clauses.append("object_type = ?")
            params.append(object_type)

        if not include_tombstoned:
            clauses.append("lifecycle != ?")
            params.append(Lifecycle.ARCHIVED.name)

        where = " AND ".join(clauses)
        row = self._backend.fetch_one(
            f"SELECT COUNT(*) as cnt FROM scoped_objects WHERE {where}",
            tuple(params),
        )
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Update (creates new version)
    # ------------------------------------------------------------------

    def update(
        self,
        object_id: str,
        *,
        principal_id: str,
        data: dict[str, Any],
        change_reason: str = "",
    ) -> tuple[ScopedObject, ObjectVersion]:
        """Update an object by creating a new version. Never modifies existing data.

        Returns (updated_object, new_version).
        Raises AccessDeniedError if principal cannot access the object.
        Raises IsolationViolationError if the object is tombstoned.
        """
        obj = self.get_or_raise(object_id, principal_id=principal_id)

        if obj.is_tombstoned:
            raise IsolationViolationError(
                f"Cannot update tombstoned object {object_id}",
                context={"object_id": object_id},
            )

        # Get previous version data for audit before_state
        prev_ver = self.get_version(object_id, obj.current_version)
        before_data = prev_ver.data if prev_ver else None

        ts = now_utc()
        new_version_num = obj.current_version + 1
        checksum = compute_checksum(data)
        ver_id = generate_id()

        ver = ObjectVersion(
            id=ver_id,
            object_id=object_id,
            version=new_version_num,
            data=data,
            created_at=ts,
            created_by=principal_id,
            change_reason=change_reason,
            checksum=checksum,
        )

        self._persist_version(ver)
        self._backend.execute(
            "UPDATE scoped_objects SET current_version = ? WHERE id = ?",
            (new_version_num, object_id),
        )

        # Return updated object
        updated_obj = ScopedObject(
            id=obj.id,
            object_type=obj.object_type,
            owner_id=obj.owner_id,
            current_version=new_version_num,
            created_at=obj.created_at,
            lifecycle=obj.lifecycle,
            registry_entry_id=obj.registry_entry_id,
        )

        if self._audit:
            self._audit.record(
                actor_id=principal_id,
                action=ActionType.UPDATE,
                target_type=obj.object_type,
                target_id=object_id,
                before_state=before_data,
                after_state=data,
            )

        return updated_obj, ver

    # ------------------------------------------------------------------
    # Tombstone (soft delete)
    # ------------------------------------------------------------------

    def tombstone(
        self,
        object_id: str,
        *,
        principal_id: str,
        reason: str = "",
    ) -> Tombstone:
        """Soft-delete an object. Object and versions remain; lifecycle set to ARCHIVED.

        Raises AccessDeniedError if principal cannot access the object.
        Raises IsolationViolationError if already tombstoned.
        """
        obj = self.get_or_raise(object_id, principal_id=principal_id)

        if obj.is_tombstoned:
            raise IsolationViolationError(
                f"Object {object_id} is already tombstoned",
                context={"object_id": object_id},
            )

        ts = now_utc()
        tomb_id = generate_id()
        tomb = Tombstone(
            id=tomb_id,
            object_id=object_id,
            tombstoned_at=ts,
            tombstoned_by=principal_id,
            reason=reason,
        )

        # Get current data for audit before_state
        cur_ver = self.get_version(object_id, obj.current_version)
        before_data = cur_ver.data if cur_ver else None

        self._backend.execute(
            "UPDATE scoped_objects SET lifecycle = ? WHERE id = ?",
            (Lifecycle.ARCHIVED.name, object_id),
        )
        self._backend.execute(
            "INSERT INTO tombstones (id, object_id, tombstoned_at, tombstoned_by, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (tomb.id, tomb.object_id, tomb.tombstoned_at.isoformat(), tomb.tombstoned_by, tomb.reason),
        )

        if self._audit:
            self._audit.record(
                actor_id=principal_id,
                action=ActionType.DELETE,
                target_type=obj.object_type,
                target_id=object_id,
                before_state=before_data,
            )

        return tomb

    def get_tombstone(self, object_id: str) -> Tombstone | None:
        """Get the tombstone marker for an object, if it exists."""
        row = self._backend.fetch_one(
            "SELECT * FROM tombstones WHERE object_id = ?", (object_id,)
        )
        if row is None:
            return None
        return Tombstone(
            id=row["id"],
            object_id=row["object_id"],
            tombstoned_at=datetime.fromisoformat(row["tombstoned_at"]),
            tombstoned_by=row["tombstoned_by"],
            reason=row["reason"],
        )

    # ------------------------------------------------------------------
    # Version access
    # ------------------------------------------------------------------

    def get_version(self, object_id: str, version: int) -> ObjectVersion | None:
        """Get a specific version of an object."""
        row = self._backend.fetch_one(
            "SELECT * FROM object_versions WHERE object_id = ? AND version = ?",
            (object_id, version),
        )
        if row is None:
            return None
        return self._row_to_version(row)

    def get_current_version(
        self, object_id: str, *, principal_id: str
    ) -> ObjectVersion | None:
        """Get the latest version of an object (isolation-enforced)."""
        obj = self.get(object_id, principal_id=principal_id)
        if obj is None:
            return None
        return self.get_version(object_id, obj.current_version)

    def list_versions(
        self, object_id: str, *, principal_id: str
    ) -> list[ObjectVersion]:
        """List all versions of an object (isolation-enforced)."""
        obj = self.get(object_id, principal_id=principal_id)
        if obj is None:
            return []
        rows = self._backend.fetch_all(
            "SELECT * FROM object_versions WHERE object_id = ? ORDER BY version ASC",
            (object_id,),
        )
        return [self._row_to_version(r) for r in rows]

    def diff(
        self,
        object_id: str,
        version_a: int,
        version_b: int,
        *,
        principal_id: str,
    ) -> dict[str, Any] | None:
        """Compute diff between two versions (isolation-enforced).

        Returns None if the principal cannot access the object.
        """
        obj = self.get(object_id, principal_id=principal_id)
        if obj is None:
            return None
        va = self.get_version(object_id, version_a)
        vb = self.get_version(object_id, version_b)
        if va is None or vb is None:
            return None
        return diff_versions(va, vb)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _persist_object(self, obj: ScopedObject) -> None:
        self._backend.execute(
            "INSERT INTO scoped_objects "
            "(id, object_type, owner_id, registry_entry_id, current_version, created_at, lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                obj.id,
                obj.object_type,
                obj.owner_id,
                obj.registry_entry_id,
                obj.current_version,
                obj.created_at.isoformat(),
                obj.lifecycle.name,
            ),
        )

    def _persist_version(self, ver: ObjectVersion) -> None:
        self._backend.execute(
            "INSERT INTO object_versions "
            "(id, object_id, version, data_json, created_at, created_by, change_reason, checksum) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ver.id,
                ver.object_id,
                ver.version,
                json.dumps(ver.data, sort_keys=True, default=str),
                ver.created_at.isoformat(),
                ver.created_by,
                ver.change_reason,
                ver.checksum,
            ),
        )

    def _load_object(self, object_id: str) -> ScopedObject | None:
        row = self._backend.fetch_one(
            "SELECT * FROM scoped_objects WHERE id = ?", (object_id,)
        )
        if row is None:
            return None
        return self._row_to_object(row)

    @staticmethod
    def _row_to_object(row: dict[str, Any]) -> ScopedObject:
        return ScopedObject(
            id=row["id"],
            object_type=row["object_type"],
            owner_id=row["owner_id"],
            current_version=row["current_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            lifecycle=Lifecycle[row["lifecycle"]],
            registry_entry_id=row.get("registry_entry_id"),
        )

    @staticmethod
    def _row_to_version(row: dict[str, Any]) -> ObjectVersion:
        return ObjectVersion(
            id=row["id"],
            object_id=row["object_id"],
            version=row["version"],
            data=json.loads(row["data_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            created_by=row["created_by"],
            change_reason=row["change_reason"],
            checksum=row["checksum"],
        )
