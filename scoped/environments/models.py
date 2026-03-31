"""Environment data models."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from scoped.types import Lifecycle, now_utc


class EnvironmentState(Enum):
    """Lifecycle states for an environment."""

    SPAWNING = "spawning"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    DISCARDED = "discarded"
    PROMOTED = "promoted"


class ObjectOrigin(Enum):
    """How an object came to be in an environment."""

    CREATED = "created"
    PROJECTED = "projected"


# Valid state transitions
VALID_TRANSITIONS: dict[EnvironmentState, frozenset[EnvironmentState]] = {
    EnvironmentState.SPAWNING: frozenset({EnvironmentState.ACTIVE}),
    EnvironmentState.ACTIVE: frozenset({
        EnvironmentState.SUSPENDED,
        EnvironmentState.COMPLETED,
    }),
    EnvironmentState.SUSPENDED: frozenset({EnvironmentState.ACTIVE}),
    EnvironmentState.COMPLETED: frozenset({
        EnvironmentState.DISCARDED,
        EnvironmentState.PROMOTED,
    }),
    EnvironmentState.DISCARDED: frozenset(),
    EnvironmentState.PROMOTED: frozenset({EnvironmentState.DISCARDED}),
}


@dataclass(slots=True)
class Environment:
    """An isolated workspace where tasks happen."""

    id: str
    name: str
    owner_id: str
    created_at: datetime
    state: EnvironmentState = EnvironmentState.SPAWNING
    description: str = ""
    template_id: str | None = None
    scope_id: str | None = None
    ephemeral: bool = True
    completed_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.state == EnvironmentState.ACTIVE

    @property
    def is_terminal(self) -> bool:
        return self.state in (EnvironmentState.DISCARDED,)

    @property
    def is_mutable(self) -> bool:
        """Can objects be created/modified in this environment?"""
        return self.state == EnvironmentState.ACTIVE

    def can_transition_to(self, target: EnvironmentState) -> bool:
        return target in VALID_TRANSITIONS.get(self.state, frozenset())

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "owner_id": self.owner_id,
            "state": self.state.value,
            "description": self.description,
            "template_id": self.template_id,
            "scope_id": self.scope_id,
            "ephemeral": self.ephemeral,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class EnvironmentTemplate:
    """Reusable blueprint for spinning up environments."""

    id: str
    name: str
    owner_id: str
    created_at: datetime
    description: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    lifecycle: Lifecycle = Lifecycle.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "owner_id": self.owner_id,
            "description": self.description,
            "config": self.config,
            "lifecycle": self.lifecycle.name,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class EnvironmentSnapshot:
    """Point-in-time capture of environment state."""

    id: str
    environment_id: str
    name: str
    snapshot_data: dict[str, Any]
    created_at: datetime
    created_by: str
    checksum: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "environment_id": self.environment_id,
            "name": self.name,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "checksum": self.checksum,
        }


@dataclass(frozen=True, slots=True)
class EnvironmentObject:
    """Tracks an object's membership in an environment."""

    id: str
    environment_id: str
    object_id: str
    origin: ObjectOrigin
    added_at: datetime

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "environment_id": self.environment_id,
            "object_id": self.object_id,
            "origin": self.origin.value,
            "added_at": self.added_at.isoformat(),
        }


def compute_snapshot_checksum(data: dict[str, Any]) -> str:
    """SHA-256 checksum of snapshot data."""
    payload = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# -- Row mapping helpers ---------------------------------------------------

def environment_from_row(row: dict[str, Any]) -> Environment:
    meta = row.get("metadata_json", "{}")
    if isinstance(meta, str):
        meta = json.loads(meta)
    completed = row.get("completed_at")
    return Environment(
        id=row["id"],
        name=row["name"],
        owner_id=row["owner_id"],
        state=EnvironmentState(row["state"]),
        description=row.get("description", ""),
        template_id=row.get("template_id"),
        scope_id=row.get("scope_id"),
        ephemeral=bool(row.get("ephemeral", 1)),
        created_at=datetime.fromisoformat(row["created_at"]),
        completed_at=datetime.fromisoformat(completed) if completed else None,
        metadata=meta,
    )


def template_from_row(row: dict[str, Any]) -> EnvironmentTemplate:
    config = row.get("config_json", "{}")
    if isinstance(config, str):
        config = json.loads(config)
    return EnvironmentTemplate(
        id=row["id"],
        name=row["name"],
        owner_id=row["owner_id"],
        description=row.get("description", ""),
        config=config,
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def snapshot_from_row(row: dict[str, Any]) -> EnvironmentSnapshot:
    data = row.get("snapshot_data", "{}")
    if isinstance(data, str):
        data = json.loads(data)
    return EnvironmentSnapshot(
        id=row["id"],
        environment_id=row["environment_id"],
        name=row.get("name", ""),
        snapshot_data=data,
        created_at=datetime.fromisoformat(row["created_at"]),
        created_by=row["created_by"],
        checksum=row.get("checksum", ""),
    )


def env_object_from_row(row: dict[str, Any]) -> EnvironmentObject:
    return EnvironmentObject(
        id=row["id"],
        environment_id=row["environment_id"],
        object_id=row["object_id"],
        origin=ObjectOrigin(row["origin"]),
        added_at=datetime.fromisoformat(row["added_at"]),
    )
