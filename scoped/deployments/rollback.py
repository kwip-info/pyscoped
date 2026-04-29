"""Deployment rollback — reversing a deployment by creating a new one.

A deployment rollback is itself a deployment. It creates a new deployment
record with ``rollback_of`` pointing to the original. This preserves
full history and means rollbacks are governed by the same gates and
traces as forward deployments.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from scoped.exceptions import DeploymentError, DeploymentRollbackError
from scoped.storage._query import compile_for
from scoped.storage._schema import deployments
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, now_utc

from scoped.deployments.executor import DeploymentExecutor
from scoped.deployments.models import Deployment, DeploymentState
from scoped._stability import stable


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
class DeploymentRollbackManager:
    """Create and manage deployment rollbacks."""

    def __init__(
        self,
        backend: StorageBackend,
        executor: DeploymentExecutor,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._executor = executor
        self._audit = audit_writer

    def rollback_deployment(
        self,
        deployment_id: str,
        *,
        actor_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> Deployment:
        """Roll back a deployment by creating a new reversal deployment.

        The original deployment must be in DEPLOYED state.
        """
        original = self._executor.get_deployment(deployment_id)
        if original is None:
            raise DeploymentRollbackError(
                f"Deployment {deployment_id} not found",
                context={"deployment_id": deployment_id},
            )
        if original.state != DeploymentState.DEPLOYED:
            raise DeploymentRollbackError(
                f"Cannot roll back deployment in state {original.state.value}",
                context={
                    "deployment_id": deployment_id,
                    "state": original.state.value,
                },
            )
        rollback_metadata = metadata or {"reason": f"rollback of {deployment_id}"}
        _validate_json_dict(rollback_metadata, "metadata")

        # Create a new rollback deployment
        rollback_dep = self._executor.create_deployment(
            target_id=original.target_id,
            deployed_by=actor_id,
            object_id=original.object_id,
            scope_id=original.scope_id,
            rollback_of=deployment_id,
            metadata=rollback_metadata,
        )

        # Mark original as rolled back
        self._executor.transition_state(
            deployment_id,
            DeploymentState.ROLLED_BACK,
            actor_id=actor_id,
        )

        if self._audit is not None:
            self._audit.record(
                actor_id=actor_id,
                action=ActionType.DEPLOY_ROLLBACK,
                target_type="deployment",
                target_id=rollback_dep.id,
                before_state=original.snapshot(),
                after_state=rollback_dep.snapshot(),
            )

        return rollback_dep

    def get_rollback_chain(self, deployment_id: str) -> list[Deployment]:
        """Get the chain of rollbacks for a deployment."""
        chain: list[Deployment] = []
        current_id: str | None = deployment_id
        seen: set[str] = set()

        while current_id is not None:
            if current_id in seen:
                raise DeploymentRollbackError(
                    f"Detected cycle in rollback chain at deployment {current_id}",
                    context={"deployment_id": deployment_id, "cycle_at": current_id},
                )
            seen.add(current_id)
            dep = self._executor.get_deployment(current_id)
            if dep is None:
                break
            chain.append(dep)
            # Find any deployment that is a rollback of this one
            stmt = (
                sa.select(deployments.c.id)
                .where(deployments.c.rollback_of == current_id)
                .order_by(deployments.c.version.asc())
            )
            sql, params = compile_for(stmt, self._backend.dialect)
            row = self._backend.fetch_one(sql, params)
            current_id = row["id"] if row else None

        return chain
