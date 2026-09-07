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

from scoped.exceptions import DeploymentError, DeploymentGateFailedError, ScopeNotFoundError
from scoped.storage._query import compile_for
from scoped.storage._schema import (
    deployment_gates,
    deployment_targets,
    deployments,
    scoped_objects,
    scopes,
)
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, generate_id, now_utc

from scoped.deployments.models import (
    Deployment,
    DeploymentState,
    DeploymentTarget,
    deployment_from_row,
    target_from_row,
)
from scoped._stability import stable

_ALLOWED_TRANSITIONS = {
    DeploymentState.PENDING: frozenset({DeploymentState.DEPLOYING}),
    DeploymentState.DEPLOYING: frozenset({DeploymentState.DEPLOYED, DeploymentState.FAILED}),
    DeploymentState.DEPLOYED: frozenset({DeploymentState.ROLLED_BACK}),
    DeploymentState.FAILED: frozenset(),
    DeploymentState.ROLLED_BACK: frozenset(),
}


def _validate_json_dict(value: dict[str, Any], field_name: str) -> None:
    """Validate that *value* is a JSON-serializable dict with string keys."""
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a dict, got {type(value).__name__}")
    for key in value:
        if not isinstance(key, str):
            raise ValueError(
                f"{field_name} keys must be strings, got {type(key).__name__}"
            )
    try:
        json.dumps(value, default=str)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is not JSON-serializable: {exc}") from exc


@stable(since="1.5.0")
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
        target_config = config or {}
        _validate_json_dict(target_config, "config")
        target = DeploymentTarget(
            id=tid,
            name=name,
            target_type=target_type,
            config=target_config,
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
        target = self._require_target(target_id, active_only=True)
        deployment_metadata = metadata or {}
        _validate_json_dict(deployment_metadata, "metadata")
        if object_id is not None:
            self._require_object(object_id)
        if scope_id is not None:
            self._require_scope(scope_id)
        if rollback_of is not None:
            original = self.get_deployment(rollback_of)
            if original is None:
                raise DeploymentError(
                    f"Rollback source deployment {rollback_of} not found",
                    context={"rollback_of": rollback_of},
                )
            if original.target_id != target_id:
                raise DeploymentError(
                    "Rollback deployment target must match the original deployment",
                    context={
                        "rollback_of": rollback_of,
                        "original_target_id": original.target_id,
                        "target_id": target_id,
                    },
                )

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
            metadata=deployment_metadata,
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
        allowed = _ALLOWED_TRANSITIONS[dep.state]
        if new_state not in allowed:
            raise DeploymentError(
                f"Invalid deployment transition {dep.state.value} -> {new_state.value}",
                context={
                    "deployment_id": deployment_id,
                    "from_state": dep.state.value,
                    "to_state": new_state.value,
                    "allowed_transitions": [state.value for state in allowed],
                },
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
        self._require_target(dep.target_id, active_only=True)

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

    def _require_target(
        self,
        target_id: str,
        *,
        active_only: bool = False,
    ) -> DeploymentTarget:
        target = self.get_target(target_id)
        if target is None:
            raise DeploymentError(
                f"Deployment target {target_id} not found",
                context={"target_id": target_id},
            )
        if active_only and not target.is_active:
            raise DeploymentError(
                f"Deployment target {target_id} is archived",
                context={"target_id": target_id, "lifecycle": target.lifecycle.name},
            )
        return target

    def _require_object(self, object_id: str) -> None:
        stmt = sa.select(scoped_objects.c.id).where(scoped_objects.c.id == object_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            raise DeploymentError(
                f"Deployment object {object_id} not found",
                context={"object_id": object_id},
            )

    def _require_scope(self, scope_id: str) -> None:
        stmt = sa.select(scopes.c.id).where(scopes.c.id == scope_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            raise ScopeNotFoundError(
                f"Scope {scope_id} not found",
                context={"scope_id": scope_id},
            )
