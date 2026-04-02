"""ScopedManager — isolation-enforcing object manager.

Every query goes through here. No raw access to objects bypasses isolation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa

from scoped.logging import get_logger
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
from scoped.storage._query import compile_for
from scoped.storage._schema import object_versions, scoped_objects, tombstones
from scoped.storage.interface import StorageBackend
from scoped.ids import ObjectId, VersionId
from scoped.types import ActionType, Lifecycle, generate_id, now_utc


_logger = get_logger("objects.manager")


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
        rule_engine: Any | None = None,
        quota_checker: Any | None = None,
        rate_limit_checker: Any | None = None,
        visibility_engine: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer
        self._rule_engine = rule_engine
        self._quota_checker = quota_checker
        self._rate_limit_checker = rate_limit_checker
        self._visibility = visibility_engine

    # ------------------------------------------------------------------
    # Rule enforcement
    # ------------------------------------------------------------------

    def _check_rules(
        self,
        *,
        action: str,
        principal_id: str,
        object_type: str | None = None,
        object_id: str | None = None,
    ) -> None:
        """Evaluate rules and raise AccessDeniedError if denied.

        No-op when no rule engine is configured or when no rules exist.
        """
        if self._rule_engine is None:
            return
        result = self._rule_engine.evaluate(
            action=action,
            principal_id=principal_id,
            object_type=object_type,
            object_id=object_id,
        )
        if not result.allowed and (result.deny_rules or result.matching_rules):
            deny_names = [r.name for r in result.deny_rules]
            raise AccessDeniedError(
                f"Action '{action}' denied by rule(s): {deny_names}",
                context={
                    "action": action,
                    "principal_id": principal_id,
                    "object_type": object_type,
                    "deny_rules": deny_names,
                },
            )

    # ------------------------------------------------------------------
    # Projection access-level enforcement
    # ------------------------------------------------------------------

    def _check_access_level(
        self,
        object_id: str,
        principal_id: str,
        *,
        required: str,  # "read", "write", "admin"
    ) -> None:
        """Check if the principal has sufficient access level on a projected object.

        Only enforced when a visibility_engine is configured. If the principal
        is the object owner, access is always granted (ownership trumps projection level).
        """
        if self._visibility is None:
            return

        # Load the object to check ownership
        obj = self._load_object(object_id)
        if obj is None:
            return

        # Owner always has full access
        if can_access(obj.owner_id, principal_id):
            return

        # Non-owner: check projection access level
        from scoped.tenancy.models import AccessLevel

        level = self._visibility.get_access_level(principal_id, object_id)

        required_levels = {
            "read": {AccessLevel.READ, AccessLevel.WRITE, AccessLevel.ADMIN},
            "write": {AccessLevel.WRITE, AccessLevel.ADMIN},
            "admin": {AccessLevel.ADMIN},
        }

        if level not in required_levels.get(required, set()):
            raise AccessDeniedError(
                f"Principal {principal_id} has {level.value if level else 'no'} access "
                f"on object {object_id}, but {required} is required",
                context={
                    "object_id": object_id,
                    "principal_id": principal_id,
                    "required_level": required,
                    "actual_level": level.value if level else None,
                },
            )

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        object_type: str,
        owner_id: str,
        data: dict[str, Any] | Any,
        registry_entry_id: str | None = None,
        change_reason: str = "created",
        scope_id: str | None = None,
    ) -> tuple[ScopedObject, ObjectVersion]:
        """Create a new scoped object with its first version.

        Returns (object, version_1).
        Quota checks run inside the write transaction to prevent TOCTOU races.

        ``data`` can be a dict or a typed instance (Pydantic model, dataclass,
        or any class registered via ``scoped.register_type()``).  Typed
        instances are auto-serialized to dicts before storage.
        """
        # Auto-serialize typed data
        if not isinstance(data, dict):
            from scoped._type_registry import _registry

            if _registry.has_type(object_type):
                data = _registry.serialize(object_type, data)
            else:
                raise TypeError(
                    f"data must be a dict or a registered type for {object_type!r}. "
                    f"Use scoped.register_type() to register a type."
                )

        self._check_rules(
            action="create", principal_id=owner_id, object_type=object_type,
        )

        # Rate limit check (approximate, outside txn — acceptable for soft limits)
        if self._rate_limit_checker is not None:
            from scoped.exceptions import RateLimitExceededError

            rl_result = self._rate_limit_checker.check(
                action="create", principal_id=owner_id, scope_id=scope_id,
            )
            if rl_result is not None and not rl_result.allowed:
                raise RateLimitExceededError(
                    f"Rate limit exceeded for create: "
                    f"{rl_result.current_count}/{rl_result.max_count}",
                    context={
                        "rule_id": rl_result.rule_id,
                        "current_count": rl_result.current_count,
                        "max_count": rl_result.max_count,
                        "window_seconds": rl_result.window_seconds,
                    },
                )

        ts = now_utc()
        obj_id = ObjectId.generate()
        ver_id = VersionId.generate()
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
            _object_type=object_type,
        )

        _logger.info(
            "object.create", object_type=object_type, owner_id=owner_id,
        )

        with self._backend.transaction() as txn:
            # Quota check inside transaction for TOCTOU safety
            if self._quota_checker is not None:
                from scoped.exceptions import QuotaExceededError

                result = self._quota_checker.check_in_txn(
                    txn, object_type=object_type, scope_id=scope_id,
                )
                if result is not None and not result.allowed:
                    txn.rollback()
                    raise QuotaExceededError(
                        f"Quota exceeded for {object_type}: "
                        f"{result.current_count}/{result.max_count}",
                        context={
                            "rule_id": result.rule_id,
                            "current_count": result.current_count,
                            "max_count": result.max_count,
                            "object_type": object_type,
                        },
                    )

            self._persist_object_in_txn(txn, obj)
            self._persist_version_in_txn(txn, ver)
            txn.commit()

        if self._audit:
            self._audit.record(
                actor_id=owner_id,
                action=ActionType.CREATE,
                target_type=object_type,
                target_id=obj_id,
                after_state=data,
            )

        return obj, ver

    def create_many(
        self,
        *,
        items: list[dict[str, Any]],
        owner_id: str,
    ) -> list[tuple[ScopedObject, ObjectVersion]]:
        """Create multiple objects atomically.

        Each item dict must have ``object_type`` and ``data`` keys.
        Optional: ``change_reason``.

        Returns list of (object, version) tuples.
        """
        results: list[tuple[ScopedObject, ObjectVersion]] = []
        ts = now_utc()

        with self._backend.transaction() as txn:
            for item in items:
                obj_id = ObjectId.generate()
                ver_id = VersionId.generate()
                object_type = item["object_type"]
                data = item["data"]
                checksum = compute_checksum(data)
                reason = item.get("change_reason", "created")

                obj = ScopedObject(
                    id=obj_id, object_type=object_type, owner_id=owner_id,
                    current_version=1, created_at=ts, lifecycle=Lifecycle.ACTIVE,
                )
                ver = ObjectVersion(
                    id=ver_id, object_id=obj_id, version=1, data=data,
                    created_at=ts, created_by=owner_id,
                    change_reason=reason, checksum=checksum,
                )

                obj_stmt = sa.insert(scoped_objects).values(
                    **self._object_values(obj),
                )
                sql, params = compile_for(obj_stmt, self._backend.dialect)
                txn.execute(sql, params)

                ver_stmt = sa.insert(object_versions).values(
                    **self._version_values(ver),
                )
                sql, params = compile_for(ver_stmt, self._backend.dialect)
                txn.execute(sql, params)
                results.append((obj, ver))
            txn.commit()

        if self._audit:
            self._audit.record_batch([
                {
                    "actor_id": owner_id,
                    "action": ActionType.CREATE,
                    "target_type": obj.object_type,
                    "target_id": obj.id,
                    "after_state": ver.data,
                }
                for obj, ver in results
            ])

        return results

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
        stmt = sa.select(scoped_objects).where(
            scoped_objects.c.owner_id == principal_id,
        )

        if object_type is not None:
            stmt = stmt.where(scoped_objects.c.object_type == object_type)

        if not include_tombstoned:
            stmt = stmt.where(scoped_objects.c.lifecycle != Lifecycle.ARCHIVED.name)

        desc = order_by.startswith("-")
        col = order_by.lstrip("-")
        if col not in self._OBJECT_ORDER_COLUMNS:
            col = "created_at"

        col_ref = scoped_objects.c[col]
        if desc:
            stmt = stmt.order_by(col_ref.desc())
        else:
            stmt = stmt.order_by(col_ref.asc())
        stmt = stmt.limit(limit).offset(offset)

        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [self._row_to_object(r) for r in rows]

    def count(
        self,
        *,
        principal_id: str,
        object_type: str | None = None,
        include_tombstoned: bool = False,
    ) -> int:
        """Count objects visible to a principal."""
        stmt = sa.select(sa.func.count().label("cnt")).select_from(
            scoped_objects,
        ).where(scoped_objects.c.owner_id == principal_id)

        if object_type is not None:
            stmt = stmt.where(scoped_objects.c.object_type == object_type)

        if not include_tombstoned:
            stmt = stmt.where(scoped_objects.c.lifecycle != Lifecycle.ARCHIVED.name)

        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Update (creates new version)
    # ------------------------------------------------------------------

    def update(
        self,
        object_id: str,
        *,
        principal_id: str,
        data: dict[str, Any] | Any,
        change_reason: str = "",
    ) -> tuple[ScopedObject, ObjectVersion]:
        """Update an object by creating a new version. Never modifies existing data.

        Returns (updated_object, new_version).
        ``data`` can be a dict or a typed instance (auto-serialized).
        Raises AccessDeniedError if principal cannot access the object.
        Raises IsolationViolationError if the object is tombstoned.
        """
        obj = self.get_or_raise(object_id, principal_id=principal_id)
        self._check_access_level(object_id, principal_id, required="write")

        # Auto-serialize typed data
        if not isinstance(data, dict):
            from scoped._type_registry import _registry

            if _registry.has_type(obj.object_type):
                data = _registry.serialize(obj.object_type, data)
            else:
                raise TypeError(
                    f"data must be a dict or a registered type for {obj.object_type!r}"
                )
        self._check_rules(
            action="update", principal_id=principal_id,
            object_type=obj.object_type, object_id=object_id,
        )

        if obj.is_tombstoned:
            raise IsolationViolationError(
                f"Cannot update tombstoned object {object_id}",
                context={"object_id": object_id},
            )

        _logger.info(
            "object.update", object_id=object_id, principal_id=principal_id,
            object_type=obj.object_type,
        )

        # Get previous version data for audit before_state
        prev_ver = self.get_version(object_id, obj.current_version)
        before_data = prev_ver.data if prev_ver else None

        ts = now_utc()
        new_version_num = obj.current_version + 1
        checksum = compute_checksum(data)
        ver_id = VersionId.generate()

        ver = ObjectVersion(
            id=ver_id,
            object_id=object_id,
            version=new_version_num,
            data=data,
            created_at=ts,
            created_by=principal_id,
            change_reason=change_reason,
            checksum=checksum,
            _object_type=obj.object_type,
        )

        with self._backend.transaction() as txn:
            self._persist_version_in_txn(txn, ver)
            upd_stmt = sa.update(scoped_objects).where(
                scoped_objects.c.id == object_id,
            ).values(current_version=new_version_num)
            sql, params = compile_for(upd_stmt, self._backend.dialect)
            txn.execute(sql, params)
            txn.commit()

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
        self._check_access_level(object_id, principal_id, required="admin")
        self._check_rules(
            action="delete", principal_id=principal_id,
            object_type=obj.object_type, object_id=object_id,
        )

        if obj.is_tombstoned:
            raise IsolationViolationError(
                f"Object {object_id} is already tombstoned",
                context={"object_id": object_id},
            )

        _logger.info(
            "object.tombstone", object_id=object_id, principal_id=principal_id,
            object_type=obj.object_type,
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

        with self._backend.transaction() as txn:
            upd_stmt = sa.update(scoped_objects).where(
                scoped_objects.c.id == object_id,
            ).values(lifecycle=Lifecycle.ARCHIVED.name)
            sql, params = compile_for(upd_stmt, self._backend.dialect)
            txn.execute(sql, params)

            ins_stmt = sa.insert(tombstones).values(
                id=tomb.id,
                object_id=tomb.object_id,
                tombstoned_at=tomb.tombstoned_at.isoformat(),
                tombstoned_by=tomb.tombstoned_by,
                reason=tomb.reason,
            )
            sql, params = compile_for(ins_stmt, self._backend.dialect)
            txn.execute(sql, params)
            txn.commit()

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
        stmt = sa.select(tombstones).where(tombstones.c.object_id == object_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
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
        stmt = sa.select(object_versions).where(
            (object_versions.c.object_id == object_id)
            & (object_versions.c.version == version)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
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
        self,
        object_id: str,
        *,
        principal_id: str,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ObjectVersion]:
        """List versions of an object (isolation-enforced).

        Args:
            limit: Maximum versions to return. ``None`` for all.
            offset: Number of versions to skip.
        """
        obj = self.get(object_id, principal_id=principal_id)
        if obj is None:
            return []
        stmt = sa.select(object_versions).where(
            object_versions.c.object_id == object_id,
        ).order_by(object_versions.c.version.asc())
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
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
        stmt = sa.insert(scoped_objects).values(**self._object_values(obj))
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

    def _persist_object_in_txn(self, txn: Any, obj: ScopedObject) -> None:
        stmt = sa.insert(scoped_objects).values(**self._object_values(obj))
        sql, params = compile_for(stmt, self._backend.dialect)
        txn.execute(sql, params)

    @staticmethod
    def _object_values(obj: ScopedObject) -> dict[str, Any]:
        return {
            "id": obj.id,
            "object_type": obj.object_type,
            "owner_id": obj.owner_id,
            "registry_entry_id": obj.registry_entry_id,
            "current_version": obj.current_version,
            "created_at": obj.created_at.isoformat(),
            "lifecycle": obj.lifecycle.name,
        }

    def _persist_version(self, ver: ObjectVersion) -> None:
        stmt = sa.insert(object_versions).values(**self._version_values(ver))
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

    def _persist_version_in_txn(self, txn: Any, ver: ObjectVersion) -> None:
        stmt = sa.insert(object_versions).values(**self._version_values(ver))
        sql, params = compile_for(stmt, self._backend.dialect)
        txn.execute(sql, params)

    @staticmethod
    def _version_values(ver: ObjectVersion) -> dict[str, Any]:
        return {
            "id": ver.id,
            "object_id": ver.object_id,
            "version": ver.version,
            "data_json": json.dumps(ver.data, sort_keys=True, default=str),
            "created_at": ver.created_at.isoformat(),
            "created_by": ver.created_by,
            "change_reason": ver.change_reason,
            "checksum": ver.checksum,
        }

    def _load_object(self, object_id: str) -> ScopedObject | None:
        stmt = sa.select(scoped_objects).where(scoped_objects.c.id == object_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
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
    @staticmethod
    def _row_to_version(
        row: dict[str, Any], *, object_type: str = "",
    ) -> ObjectVersion:
        return ObjectVersion(
            id=row["id"],
            object_id=row["object_id"],
            version=row["version"],
            data=json.loads(row["data_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            created_by=row["created_by"],
            change_reason=row["change_reason"],
            checksum=row["checksum"],
            _object_type=object_type,
        )
