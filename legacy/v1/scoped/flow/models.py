"""Flow data models — pipelines, stages, transitions, channels, promotions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from scoped.types import Lifecycle


class FlowPointType(Enum):
    """Types of source/target for flow channels."""

    ENVIRONMENT = "environment"
    SCOPE = "scope"
    STAGE = "stage"


@dataclass(slots=True)
class Pipeline:
    """A named sequence of stages defining how work matures."""

    id: str
    name: str
    owner_id: str
    created_at: datetime
    description: str = ""
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
            "lifecycle": self.lifecycle.name,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(slots=True)
class Stage:
    """A named state within a pipeline."""

    id: str
    pipeline_id: str
    name: str
    ordinal: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pipeline_id": self.pipeline_id,
            "name": self.name,
            "ordinal": self.ordinal,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class StageTransition:
    """Record of an object moving between stages."""

    id: str
    object_id: str
    to_stage_id: str
    transitioned_at: datetime
    transitioned_by: str
    from_stage_id: str | None = None
    reason: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object_id": self.object_id,
            "from_stage_id": self.from_stage_id,
            "to_stage_id": self.to_stage_id,
            "transitioned_at": self.transitioned_at.isoformat(),
            "transitioned_by": self.transitioned_by,
            "reason": self.reason,
        }


@dataclass(slots=True)
class FlowChannel:
    """A directional pipe between two points in the system."""

    id: str
    name: str
    source_type: FlowPointType
    source_id: str
    target_type: FlowPointType
    target_id: str
    owner_id: str
    created_at: datetime
    allowed_types: list[str] = field(default_factory=list)
    lifecycle: Lifecycle = Lifecycle.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    def allows_type(self, object_type: str) -> bool:
        """Check if this channel permits the given object type."""
        if not self.allowed_types:
            return True  # empty = allow all
        return object_type in self.allowed_types

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "source_type": self.source_type.value,
            "source_id": self.source_id,
            "target_type": self.target_type.value,
            "target_id": self.target_id,
            "allowed_types": self.allowed_types,
            "owner_id": self.owner_id,
            "lifecycle": self.lifecycle.name,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class Promotion:
    """Record of promoting an object from an environment into a scope."""

    id: str
    object_id: str
    source_env_id: str
    target_scope_id: str
    promoted_at: datetime
    promoted_by: str
    target_stage_id: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object_id": self.object_id,
            "source_env_id": self.source_env_id,
            "target_scope_id": self.target_scope_id,
            "target_stage_id": self.target_stage_id,
            "promoted_at": self.promoted_at.isoformat(),
            "promoted_by": self.promoted_by,
        }


# -- Row mapping helpers ---------------------------------------------------

def pipeline_from_row(row: dict[str, Any]) -> Pipeline:
    return Pipeline(
        id=row["id"],
        name=row["name"],
        owner_id=row["owner_id"],
        description=row.get("description", ""),
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def stage_from_row(row: dict[str, Any]) -> Stage:
    meta = row.get("metadata_json", "{}")
    if isinstance(meta, str):
        meta = json.loads(meta)
    return Stage(
        id=row["id"],
        pipeline_id=row["pipeline_id"],
        name=row["name"],
        ordinal=row["ordinal"],
        metadata=meta,
    )


def transition_from_row(row: dict[str, Any]) -> StageTransition:
    return StageTransition(
        id=row["id"],
        object_id=row["object_id"],
        from_stage_id=row.get("from_stage_id"),
        to_stage_id=row["to_stage_id"],
        transitioned_at=datetime.fromisoformat(row["transitioned_at"]),
        transitioned_by=row["transitioned_by"],
        reason=row.get("reason", ""),
    )


def channel_from_row(row: dict[str, Any]) -> FlowChannel:
    allowed = row.get("allowed_types", "[]")
    if isinstance(allowed, str):
        allowed = json.loads(allowed)
    return FlowChannel(
        id=row["id"],
        name=row["name"],
        source_type=FlowPointType(row["source_type"]),
        source_id=row["source_id"],
        target_type=FlowPointType(row["target_type"]),
        target_id=row["target_id"],
        allowed_types=allowed,
        owner_id=row["owner_id"],
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def promotion_from_row(row: dict[str, Any]) -> Promotion:
    return Promotion(
        id=row["id"],
        object_id=row["object_id"],
        source_env_id=row["source_env_id"],
        target_scope_id=row["target_scope_id"],
        target_stage_id=row.get("target_stage_id"),
        promoted_at=datetime.fromisoformat(row["promoted_at"]),
        promoted_by=row["promoted_by"],
    )
