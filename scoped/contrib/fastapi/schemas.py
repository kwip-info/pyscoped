"""Pydantic schemas bridging Scoped dataclasses to FastAPI responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class PrincipalSchema(BaseModel):
    id: str
    kind: str
    display_name: str
    created_at: datetime
    lifecycle: str

    @classmethod
    def from_principal(cls, p) -> PrincipalSchema:
        return cls(
            id=p.id,
            kind=p.kind,
            display_name=p.display_name,
            created_at=p.created_at,
            lifecycle=p.lifecycle.name if hasattr(p.lifecycle, "name") else str(p.lifecycle),
        )


class ScopedObjectSchema(BaseModel):
    id: str
    object_type: str
    owner_id: str
    current_version: int
    created_at: datetime
    lifecycle: str

    @classmethod
    def from_object(cls, obj) -> ScopedObjectSchema:
        return cls(
            id=obj.id,
            object_type=obj.object_type,
            owner_id=obj.owner_id,
            current_version=obj.current_version,
            created_at=obj.created_at,
            lifecycle=obj.lifecycle.name if hasattr(obj.lifecycle, "name") else str(obj.lifecycle),
        )


class ScopeSchema(BaseModel):
    id: str
    name: str
    owner_id: str
    lifecycle: str
    created_at: datetime

    @classmethod
    def from_scope(cls, scope) -> ScopeSchema:
        return cls(
            id=scope.id,
            name=scope.name,
            owner_id=scope.owner_id,
            lifecycle=(
                scope.lifecycle.name if hasattr(scope.lifecycle, "name") else str(scope.lifecycle)
            ),
            created_at=scope.created_at,
        )


class TraceEntrySchema(BaseModel):
    id: str
    sequence: int
    actor_id: str
    action: str
    target_type: str
    target_id: str
    timestamp: datetime
    hash: str

    @classmethod
    def from_entry(cls, entry) -> TraceEntrySchema:
        return cls(
            id=entry.id,
            sequence=entry.sequence,
            actor_id=entry.actor_id,
            action=entry.action.value if hasattr(entry.action, "value") else str(entry.action),
            target_type=entry.target_type,
            target_id=entry.target_id,
            timestamp=entry.timestamp,
            hash=entry.hash,
        )


class HealthCheckSchema(BaseModel):
    name: str
    passed: bool
    detail: str


class HealthStatusSchema(BaseModel):
    healthy: bool
    checks: dict[str, HealthCheckSchema]
