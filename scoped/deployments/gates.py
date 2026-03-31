"""Pre-deployment gate checks.

Gates are checks that must pass before a deployment proceeds.
Each gate check is recorded with its result. All gates must pass
for a deployment to execute.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, generate_id, now_utc

from scoped.deployments.models import (
    DeploymentGate,
    GateType,
    gate_from_row,
)


@dataclass(frozen=True, slots=True)
class GateResult:
    """Summary of all gate checks for a deployment."""

    deployment_id: str
    all_passed: bool
    gates: tuple[DeploymentGate, ...]
    failed_count: int = 0


class GateChecker:
    """Record and evaluate deployment gate checks."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer

    def record_gate(
        self,
        *,
        deployment_id: str,
        gate_type: GateType,
        passed: bool,
        details: dict[str, Any] | None = None,
        checked_by: str | None = None,
    ) -> DeploymentGate:
        """Record a gate check result."""
        ts = now_utc()
        gid = generate_id()
        gate = DeploymentGate(
            id=gid,
            deployment_id=deployment_id,
            gate_type=gate_type,
            passed=passed,
            checked_at=ts,
            details=details or {},
        )
        self._backend.execute(
            """INSERT INTO deployment_gates
               (id, deployment_id, gate_type, passed, checked_at, details_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (gid, deployment_id, gate_type.value, int(passed),
             ts.isoformat(), json.dumps(gate.details)),
        )

        if self._audit is not None and checked_by is not None:
            self._audit.record(
                actor_id=checked_by,
                action=ActionType.GATE_CHECK,
                target_type="deployment_gate",
                target_id=gid,
                after_state=gate.snapshot(),
            )

        return gate

    def get_gates(self, deployment_id: str) -> list[DeploymentGate]:
        """Get all gate checks for a deployment."""
        rows = self._backend.fetch_all(
            "SELECT * FROM deployment_gates WHERE deployment_id = ? ORDER BY checked_at",
            (deployment_id,),
        )
        return [gate_from_row(r) for r in rows]

    def check_all_passed(self, deployment_id: str) -> GateResult:
        """Evaluate whether all gates pass for a deployment."""
        gates = self.get_gates(deployment_id)
        failed = [g for g in gates if not g.passed]
        return GateResult(
            deployment_id=deployment_id,
            all_passed=len(failed) == 0 and len(gates) > 0,
            gates=tuple(gates),
            failed_count=len(failed),
        )

    def check_all_passed_or_none(self, deployment_id: str) -> GateResult:
        """Like check_all_passed but also passes when no gates exist."""
        gates = self.get_gates(deployment_id)
        failed = [g for g in gates if not g.passed]
        return GateResult(
            deployment_id=deployment_id,
            all_passed=len(failed) == 0,
            gates=tuple(gates),
            failed_count=len(failed),
        )
