"""Data models for Layer 3: Object Versioning & Isolation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from scoped.types import Lifecycle


@dataclass(slots=True)
class ScopedObject:
    """Envelope around any data — identity, ownership, and versioning metadata.

    A ScopedObject doesn't contain data itself; versions hold the actual state.
    """

    id: str
    object_type: str
    owner_id: str
    current_version: int
    created_at: datetime
    lifecycle: Lifecycle = Lifecycle.ACTIVE
    registry_entry_id: str | None = None

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    @property
    def is_tombstoned(self) -> bool:
        return self.lifecycle == Lifecycle.ARCHIVED

    def snapshot(self) -> dict[str, Any]:
        """Serializable snapshot for audit/versioning."""
        return {
            "id": self.id,
            "object_type": self.object_type,
            "owner_id": self.owner_id,
            "current_version": self.current_version,
            "created_at": self.created_at.isoformat(),
            "lifecycle": self.lifecycle.name,
            "registry_entry_id": self.registry_entry_id,
        }


@dataclass(frozen=True, slots=True)
class ObjectVersion:
    """Immutable snapshot of an object's state at a point in time."""

    id: str
    object_id: str
    version: int
    data: dict[str, Any]
    created_at: datetime
    created_by: str
    change_reason: str = ""
    checksum: str = ""

    def snapshot(self) -> dict[str, Any]:
        """Serializable snapshot."""
        return {
            "id": self.id,
            "object_id": self.object_id,
            "version": self.version,
            "data": self.data,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "change_reason": self.change_reason,
            "checksum": self.checksum,
        }


@dataclass(frozen=True, slots=True)
class Tombstone:
    """Soft-deletion marker. The object and versions remain; this marks it dead."""

    id: str
    object_id: str
    tombstoned_at: datetime
    tombstoned_by: str
    reason: str = ""


def compute_checksum(data: dict[str, Any]) -> str:
    """Compute a SHA-256 checksum of serialized object data."""
    payload = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
