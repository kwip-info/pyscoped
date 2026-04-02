"""Deployment executor — create, advance, and manage deployments.

The executor handles the lifecycle of deployments: creating them,
transitioning their state, and recording the results. The actual
deployment action is abstract — applications provide their own
implementation via callbacks.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import sqlalchemy as sa

from scoped.exceptions import DeploymentError, DeploymentGateFailedError
from scoped.storage._query import compile_for
from scoped.storage._schema import deployment_gates, deployment_targets, deployments
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, generate_id, now_utc

from scoped.deployments.models import (
    Deployment,
    DeploymentState,
    DeploymentTarget,
    deployment_from_row,
    target_from_row,
)
from scoped._stability import experimental


@experimental()
class DeploymentExecutor:
    """Create and manage deployments and their targets."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer

    # -- Targets -----------------------------------------------------------

    def create_target(
        self,
        *,
        name: str,
        target_type: str,
        owner_id: str,
        config: dict[str, Any] | None = None,
    ) -> DeploymentTarget:
        ts = now_utc()
        tid = generate_id()
        target = DeploymentTarget(
            id=tid,
            name=name,
            target_type=target_type,
            config=config or {},
            owner_id=owner_id,
            created_at=ts,
        )
        stmt = sa.insert(deployment_targets).values(
            id=tid, name=name, target_type=target_type,
            config_json=json.dumps(target.config),
            owner_id=owner_id, created_at=ts.isoformat(),
            lifecycle="ACTIVE",
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit is not None:
            self._audit.record(
                actor_id=owner_id,
                action=ActionType.CREATE,
                target_type="deployment_target",
                target_id=tid,
                after_state={"name": name, "target_type": target_type},
            )

        return target

    def get_target(self, target_id: str) -> DeploymentTarget | None:
        stmt = sa.select(deployment_targets).where(deployment_targets.c.id == target_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return target_from_row(row) if row else None

    def list_targets(
        self,
        *,
        owner_id: str | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[DeploymentTarget]:
        stmt = sa.select(deployment_targets)
        if owner_id is not None:
            stmt = stmt.where(deployment_targets.c.owner_id == owner_id)
        if active_only:
            stmt = stmt.where(deployment_targets.c.lifecycle == "ACTIVE")
        stmt = stmt.order_by(deployment_targets.c.created_at.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [target_from_row(r) for r in rows]

    def archive_target(self, target_id: str, *, archived_by: str | None = None) -> None:
        stmt = (
            sa.update(deployment_targets)
            .where(deployment_targets.c.id == target_id)
            .values(lifecycle="ARCHIVED")
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit is not None and archived_by is not None:
            self._audit.record(
                actor_id=archived_by,
                action=ActionType.LIFECYCLE_CHANGE,
                target_type="deployment_target",
                target_id=target_id,
                before_state={"lifecycle": "ACTIVE"},
                after_state={"lifecycle": "ARCHIVED"},
            )

    # -- Deployments -------------------------------------------------------

    def create_deployment(
        self,
        *,
        target_id: str,
        deployed_by: str,
        object_id: str | None = None,
        scope_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        rollback_of: str | None = None,
    ) -> Deployment:
        """Create a new deployment in PENDING state."""
        # Compute version number for this target
        stmt = (
            sa.select(sa.func.coalesce(sa.func.max(deployments.c.version), 0).label("max_v"))
            .where(deployments.c.target_id == target_id)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        next_version = (row["max_v"] if row else 0) + 1

        ts = now_utc()
        did = generate_id()
        dep = Deployment(
            id=did,
            target_id=target_id,
            object_id=object_id,
            scope_id=scope_id,
            version=next_version,
            state=DeploymentState.PENDING,
            deployed_by=deployed_by,
            rollback_of=rollback_of,
            metadata=metadata or {},
        )
        stmt = sa.insert(deployments).values(
            id=did, target_id=target_id, object_id=object_id,
            scope_id=scope_id, version=next_version,
            state=DeploymentState.PENDING.value,
            deployed_by=deployed_by, rollback_of=rollback_of,
            metadata_json=json.dumps(dep.metadata),
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit is not None:
            self._audit.record(
                actor_id=deployed_by,
                action=ActionType.DEPLOY,
                target_type="deployment",
                target_id=did,
                after_state=dep.snapshot(),
            )

        return dep

    def get_deployment(self, deployment_id: str) -> Deployment | None:
        stmt = sa.select(deployments).where(deployments.c.id == deployment_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return deployment_from_row(row) if row else None

    def list_deployments(
        self,
        *,
        target_id: str | None = None,
        state: DeploymentState | None = None,
        limit: int = 100,
    ) -> list[Deployment]:
        stmt = sa.select(deployments)
        if target_id is not None:
            stmt = stmt.where(deployments.c.target_id == target_id)
        if state is not None:
            stmt = stmt.where(deployments.c.state == state.value)
        stmt = stmt.order_by(deployments.c.version.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [deployment_from_row(r) for r in rows]

    def transition_state(
        self,
        deployment_id: str,
        new_state: DeploymentState,
        *,
        actor_id: str | None = None,
    ) -> Deployment:
        """Move a deployment to a new state."""
        dep = self.get_deployment(deployment_id)
        if dep is None:
            raise DeploymentError(
                f"Deployment {deployment_id} not found",
                context={"deployment_id": deployment_id},
            )
        # Allow DEPLOYED → ROLLED_BACK (rollback path), block other terminal transitions
        if dep.is_terminal:
            if not (dep.state == DeploymentState.DEPLOYED and new_state == DeploymentState.ROLLED_BACK):
                raise DeploymentError(
                    f"Deployment {deployment_id} is in terminal state {dep.state.value}",
                    context={"deployment_id": deployment_id, "state": dep.state.value},
                )

        before = dep.snapshot()
        dep.state = new_state
        values: dict[str, Any] = {"state": new_state.value}

        if new_state == DeploymentState.DEPLOYED:
            ts = now_utc()
            dep.deployed_at = ts
            values["deployed_at"] = ts.isoformat()

        stmt = (
            sa.update(deployments)
            .where(deployments.c.id == deployment_id)
            .values(**values)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit is not None and actor_id is not None:
            self._audit.record(
                actor_id=actor_id,
                action=ActionType.DEPLOY,
                target_type="deployment",
                target_id=deployment_id,
                before_state=before,
                after_state=dep.snapshot(),
            )

        return dep

    def execute_deployment(
        self,
        deployment_id: str,
        *,
        actor_id: str,
        deploy_fn: Callable[[Deployment], bool] | None = None,
    ) -> Deployment:
        """Run a deployment through the full lifecycle.

        1. Check all gates pass
        2. Transition to DEPLOYING
        3. Call deploy_fn (if provided)
        4. Transition to DEPLOYED or FAILED
        """
        dep = self.get_deployment(deployment_id)
        if dep is None:
            raise DeploymentError(
                f"Deployment {deployment_id} not found",
                context={"deployment_id": deployment_id},
            )
        if dep.state != DeploymentState.PENDING:
            raise DeploymentError(
                f"Deployment must be in PENDING state, got {dep.state.value}",
                context={"deployment_id": deployment_id, "state": dep.state.value},
            )

        # Check gates
        stmt = (
            sa.select(deployment_gates)
            .where(deployment_gates.c.deployment_id == deployment_id)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        gates = self._backend.fetch_all(sql, params)
        if gates:
            failed = [g for g in gates if not g["passed"]]
            if failed:
                raise DeploymentGateFailedError(
                    f"{len(failed)} gate(s) failed for deployment {deployment_id}",
                    context={
                        "deployment_id": deployment_id,
                        "failed_gates": [g["id"] for g in failed],
                    },
                )

        # Transition to deploying
        dep = self.transition_state(deployment_id, DeploymentState.DEPLOYING, actor_id=actor_id)

        # Execute
        if deploy_fn is not None:
            try:
                success = deploy_fn(dep)
            except Exception:
                dep = self.transition_state(deployment_id, DeploymentState.FAILED, actor_id=actor_id)
                return dep
            if not success:
                dep = self.transition_state(deployment_id, DeploymentState.FAILED, actor_id=actor_id)
                return dep

        # Success
        dep = self.transition_state(deployment_id, DeploymentState.DEPLOYED, actor_id=actor_id)
        return dep
