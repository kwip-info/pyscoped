"""Connector data models — connectors, policies, traffic records."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ConnectorState(Enum):
    """Connector lifecycle states."""

    PROPOSED = "proposed"
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    REJECTED = "rejected"


# Valid state transitions
VALID_CONNECTOR_TRANSITIONS: dict[ConnectorState, frozenset[ConnectorState]] = {
    ConnectorState.PROPOSED: frozenset({
        ConnectorState.PENDING_APPROVAL, ConnectorState.REJECTED, ConnectorState.REVOKED,
    }),
    ConnectorState.PENDING_APPROVAL: frozenset({
        ConnectorState.ACTIVE, ConnectorState.REJECTED, ConnectorState.REVOKED,
    }),
    ConnectorState.ACTIVE: frozenset({
        ConnectorState.SUSPENDED, ConnectorState.REVOKED,
    }),
    ConnectorState.SUSPENDED: frozenset({
        ConnectorState.ACTIVE, ConnectorState.REVOKED,
    }),
    ConnectorState.REVOKED: frozenset(),
    ConnectorState.REJECTED: frozenset(),
}

TERMINAL_CONNECTOR_STATES = frozenset({ConnectorState.REVOKED, ConnectorState.REJECTED})


class ConnectorDirection(Enum):
    """Direction of data flow through a connector."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    BIDIRECTIONAL = "bidirectional"


class PolicyType(Enum):
    """Types of connector policies."""

    ALLOW_TYPES = "allow_types"
    DENY_TYPES = "deny_types"
    RATE_LIMIT = "rate_limit"
    CLASSIFICATION = "classification"


class TrafficStatus(Enum):
    """Status of a connector traffic record."""

    SUCCESS = "success"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(slots=True)
class Connector:
    """A governed bridge between two Scoped instances."""

    id: str
    name: str
    local_org_id: str
    remote_org_id: str
    remote_endpoint: str
    created_at: datetime
    created_by: str
    description: str = ""
    state: ConnectorState = ConnectorState.PROPOSED
    direction: ConnectorDirection = ConnectorDirection.BIDIRECTIONAL
    local_scope_id: str | None = None
    approved_at: datetime | None = None
    approved_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.state == ConnectorState.ACTIVE

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_CONNECTOR_STATES

    def can_transition_to(self, target: ConnectorState) -> bool:
        return target in VALID_CONNECTOR_TRANSITIONS.get(self.state, frozenset())

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "local_org_id": self.local_org_id,
            "remote_org_id": self.remote_org_id,
            "remote_endpoint": self.remote_endpoint,
            "state": self.state.value,
            "direction": self.direction.value,
            "local_scope_id": self.local_scope_id,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "approved_by": self.approved_by,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class ConnectorPolicy:
    """A policy governing what flows through a connector."""

    id: str
    connector_id: str
    policy_type: PolicyType
    config: dict[str, Any]
    created_at: datetime
    created_by: str

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "connector_id": self.connector_id,
            "policy_type": self.policy_type.value,
            "config": self.config,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
        }


@dataclass(frozen=True, slots=True)
class ConnectorTraffic:
    """A record of data flowing through a connector."""

    id: str
    connector_id: str
    direction: str
    object_type: str
    action: str
    timestamp: datetime
    object_id: str | None = None
    status: TrafficStatus = TrafficStatus.SUCCESS
    size_bytes: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# -- Row mapping helpers ---------------------------------------------------

def connector_from_row(row: dict[str, Any]) -> Connector:
    meta = row.get("metadata_json", "{}")
    if isinstance(meta, str):
        meta = json.loads(meta)
    approved = row.get("approved_at")
    return Connector(
        id=row["id"],
        name=row["name"],
        description=row.get("description", ""),
        local_org_id=row["local_org_id"],
        remote_org_id=row["remote_org_id"],
        remote_endpoint=row["remote_endpoint"],
        state=ConnectorState(row.get("state", "proposed")),
        direction=ConnectorDirection(row.get("direction", "bidirectional")),
        local_scope_id=row.get("local_scope_id"),
        created_at=datetime.fromisoformat(row["created_at"]),
        created_by=row["created_by"],
        approved_at=datetime.fromisoformat(approved) if approved else None,
        approved_by=row.get("approved_by"),
        metadata=meta,
    )


def policy_from_row(row: dict[str, Any]) -> ConnectorPolicy:
    config = row.get("config_json", "{}")
    if isinstance(config, str):
        config = json.loads(config)
    return ConnectorPolicy(
        id=row["id"],
        connector_id=row["connector_id"],
        policy_type=PolicyType(row["policy_type"]),
        config=config,
        created_at=datetime.fromisoformat(row["created_at"]),
        created_by=row["created_by"],
    )


def traffic_from_row(row: dict[str, Any]) -> ConnectorTraffic:
    meta = row.get("metadata_json", "{}")
    if isinstance(meta, str):
        meta = json.loads(meta)
    return ConnectorTraffic(
        id=row["id"],
        connector_id=row["connector_id"],
        direction=row["direction"],
        object_type=row["object_type"],
        object_id=row.get("object_id"),
        action=row["action"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        status=TrafficStatus(row.get("status", "success")),
        size_bytes=row.get("size_bytes"),
        metadata=meta,
    )
