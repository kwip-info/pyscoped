"""Deployment data models — targets, deployments, gates."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from scoped.types import Lifecycle


class DeploymentState(Enum):
    """States a deployment moves through."""

    PENDING = "pending"
    DEPLOYING = "deploying"
    DEPLOYED = "deployed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


TERMINAL_STATES = frozenset({
    DeploymentState.DEPLOYED,
    DeploymentState.FAILED,
    DeploymentState.ROLLED_BACK,
})


class GateType(Enum):
    """Types of pre-deployment gate checks."""

    STAGE_CHECK = "stage_check"
    RULE_CHECK = "rule_check"
    APPROVAL = "approval"
    CUSTOM = "custom"


@dataclass(slots=True)
class DeploymentTarget:
    """A registered destination where deployments go."""

    id: str
    name: str
    target_type: str
    owner_id: str
    created_at: datetime
    config: dict[str, Any] = field(default_factory=dict)
    lifecycle: Lifecycle = Lifecycle.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "target_type": self.target_type,
            "config": self.config,
            "owner_id": self.owner_id,
            "lifecycle": self.lifecycle.name,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(slots=True)
class Deployment:
    """A record of work being pushed to a target."""

    id: str
    target_id: str
    deployed_by: str
    version: int = 1
    state: DeploymentState = DeploymentState.PENDING
    object_id: str | None = None
    scope_id: str | None = None
    deployed_at: datetime | None = None
    rollback_of: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target_id": self.target_id,
            "object_id": self.object_id,
            "scope_id": self.scope_id,
            "version": self.version,
            "state": self.state.value,
            "deployed_at": self.deployed_at.isoformat() if self.deployed_at else None,
            "deployed_by": self.deployed_by,
            "rollback_of": self.rollback_of,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class DeploymentGate:
    """A gate check result for a deployment."""

    id: str
    deployment_id: str
    gate_type: GateType
    passed: bool
    checked_at: datetime
    details: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "deployment_id": self.deployment_id,
            "gate_type": self.gate_type.value,
            "passed": self.passed,
            "checked_at": self.checked_at.isoformat(),
            "details": self.details,
        }


# -- Row mapping helpers ---------------------------------------------------

def target_from_row(row: dict[str, Any]) -> DeploymentTarget:
    config = row.get("config_json", "{}")
    if isinstance(config, str):
        config = json.loads(config)
    return DeploymentTarget(
        id=row["id"],
        name=row["name"],
        target_type=row["target_type"],
        config=config,
        owner_id=row["owner_id"],
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def deployment_from_row(row: dict[str, Any]) -> Deployment:
    meta = row.get("metadata_json", "{}")
    if isinstance(meta, str):
        meta = json.loads(meta)
    deployed_at = row.get("deployed_at")
    return Deployment(
        id=row["id"],
        target_id=row["target_id"],
        object_id=row.get("object_id"),
        scope_id=row.get("scope_id"),
        version=row.get("version", 1),
        state=DeploymentState(row.get("state", "pending")),
        deployed_at=datetime.fromisoformat(deployed_at) if deployed_at else None,
        deployed_by=row["deployed_by"],
        rollback_of=row.get("rollback_of"),
        metadata=meta,
    )


def gate_from_row(row: dict[str, Any]) -> DeploymentGate:
    details = row.get("details_json", "{}")
    if isinstance(details, str):
        details = json.loads(details)
    return DeploymentGate(
        id=row["id"],
        deployment_id=row["deployment_id"],
        gate_type=GateType(row["gate_type"]),
        passed=bool(row["passed"]),
        checked_at=datetime.fromisoformat(row["checked_at"]),
        details=details,
    )
