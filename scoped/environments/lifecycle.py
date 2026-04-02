"""Environment lifecycle management.

Handles spawning, activating, suspending, completing, discarding,
and promoting environments.  Each environment gets its own auto-created
isolation scope via the tenancy layer.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from scoped.exceptions import (
    EnvironmentNotFoundError,
    EnvironmentStateError,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import environments, environment_templates
from scoped.storage.interface import StorageBackend
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.types import ActionType, generate_id, now_utc

from scoped.environments.models import (
    Environment,
    EnvironmentState,
    EnvironmentTemplate,
    environment_from_row,
    template_from_row,
)
from scoped._stability import experimental


@experimental()
class EnvironmentLifecycle:
    """Manages environment creation and state transitions."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer
        self._scope_lifecycle = ScopeLifecycle(backend, audit_writer=audit_writer)

    # ------------------------------------------------------------------
    # Spawn
    # ------------------------------------------------------------------

    def spawn(
        self,
        *,
        name: str,
        owner_id: str,
        description: str = "",
        template_id: str | None = None,
        ephemeral: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> Environment:
        """Spawn a new environment in SPAWNING state with an auto-created scope.

        Call :meth:`activate` to move it to ACTIVE.
        """
        ts = now_utc()
        env_id = generate_id()
        meta = metadata or {}

        # Auto-create isolation scope for this environment
        scope = self._scope_lifecycle.create_scope(
            name=f"env:{name}",
            owner_id=owner_id,
            description=f"Auto-created scope for environment {env_id}",
        )

        env = Environment(
            id=env_id,
            name=name,
            owner_id=owner_id,
            description=description,
            template_id=template_id,
            scope_id=scope.id,
            state=EnvironmentState.SPAWNING,
            ephemeral=ephemeral,
            created_at=ts,
            metadata=meta,
        )

        stmt = sa.insert(environments).values(
            id=env.id, name=env.name, description=env.description,
            owner_id=env.owner_id, template_id=env.template_id,
            scope_id=env.scope_id, state=env.state.value,
            ephemeral=1 if env.ephemeral else 0,
            created_at=env.created_at.isoformat(),
            metadata_json=json.dumps(meta),
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        self._trace(
            actor_id=owner_id,
            action=ActionType.ENV_SPAWN,
            env=env,
            after_state=env.snapshot(),
        )

        return env

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def activate(self, env_id: str, *, actor_id: str) -> Environment:
        """Move environment from SPAWNING → ACTIVE."""
        return self._transition(env_id, EnvironmentState.ACTIVE, actor_id=actor_id)

    def suspend(self, env_id: str, *, actor_id: str) -> Environment:
        """Move environment from ACTIVE → SUSPENDED."""
        return self._transition(env_id, EnvironmentState.SUSPENDED, actor_id=actor_id)

    def resume(self, env_id: str, *, actor_id: str) -> Environment:
        """Move environment from SUSPENDED → ACTIVE."""
        return self._transition(env_id, EnvironmentState.ACTIVE, actor_id=actor_id)

    def complete(self, env_id: str, *, actor_id: str) -> Environment:
        """Move environment from ACTIVE → COMPLETED."""
        env = self._transition(env_id, EnvironmentState.COMPLETED, actor_id=actor_id)
        # Record completion timestamp
        stmt = (
            sa.update(environments)
            .where(environments.c.id == env_id)
            .values(completed_at=now_utc().isoformat())
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
        env.completed_at = now_utc()
        return env

    def discard(self, env_id: str, *, actor_id: str) -> Environment:
        """Discard the environment — archive its scope and contents.

        Valid from COMPLETED or PROMOTED states.
        """
        env = self._transition(env_id, EnvironmentState.DISCARDED, actor_id=actor_id)

        # Archive the environment's scope (which archives memberships + projections)
        if env.scope_id:
            self._scope_lifecycle.archive_scope(env.scope_id, archived_by=actor_id)

        return env

    def promote(self, env_id: str, *, actor_id: str) -> Environment:
        """Mark environment as promoted (results have been kept).

        Valid from COMPLETED state.  After promotion, the environment
        can still be discarded.
        """
        return self._transition(env_id, EnvironmentState.PROMOTED, actor_id=actor_id)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, env_id: str) -> Environment | None:
        """Fetch an environment by ID."""
        stmt = sa.select(environments).where(environments.c.id == env_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return environment_from_row(row) if row else None

    def get_or_raise(self, env_id: str) -> Environment:
        """Fetch an environment or raise :class:`EnvironmentNotFoundError`."""
        env = self.get(env_id)
        if env is None:
            raise EnvironmentNotFoundError(
                f"Environment '{env_id}' not found",
                context={"environment_id": env_id},
            )
        return env

    def list_environments(
        self,
        *,
        owner_id: str | None = None,
        state: EnvironmentState | None = None,
        ephemeral: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Environment]:
        """List environments with optional filters."""
        stmt = sa.select(environments)
        if owner_id is not None:
            stmt = stmt.where(environments.c.owner_id == owner_id)
        if state is not None:
            stmt = stmt.where(environments.c.state == state.value)
        if ephemeral is not None:
            stmt = stmt.where(environments.c.ephemeral == (1 if ephemeral else 0))
        stmt = stmt.order_by(environments.c.created_at.desc()).limit(limit).offset(offset)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [environment_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------

    def create_template(
        self,
        *,
        name: str,
        owner_id: str,
        description: str = "",
        config: dict[str, Any] | None = None,
    ) -> EnvironmentTemplate:
        """Create a reusable environment template."""
        ts = now_utc()
        tmpl_id = generate_id()
        cfg = config or {}

        tmpl = EnvironmentTemplate(
            id=tmpl_id,
            name=name,
            owner_id=owner_id,
            description=description,
            config=cfg,
            created_at=ts,
        )

        stmt = sa.insert(environment_templates).values(
            id=tmpl.id, name=tmpl.name, description=tmpl.description,
            owner_id=tmpl.owner_id, config_json=json.dumps(cfg),
            created_at=tmpl.created_at.isoformat(),
            lifecycle=tmpl.lifecycle.name,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
        return tmpl

    def get_template(self, template_id: str) -> EnvironmentTemplate | None:
        """Fetch a template by ID."""
        stmt = sa.select(environment_templates).where(environment_templates.c.id == template_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return template_from_row(row) if row else None

    def list_templates(
        self,
        *,
        owner_id: str | None = None,
        limit: int = 100,
    ) -> list[EnvironmentTemplate]:
        """List templates, optionally filtered by owner."""
        stmt = (
            sa.select(environment_templates)
            .where(environment_templates.c.lifecycle == "ACTIVE")
        )
        if owner_id:
            stmt = stmt.where(environment_templates.c.owner_id == owner_id)
        stmt = stmt.order_by(environment_templates.c.created_at.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [template_from_row(r) for r in rows]

    def spawn_from_template(
        self,
        template_id: str,
        *,
        owner_id: str,
        name: str | None = None,
    ) -> Environment:
        """Spawn an environment from a template."""
        tmpl = self.get_template(template_id)
        if tmpl is None:
            raise EnvironmentNotFoundError(
                f"Template '{template_id}' not found",
                context={"template_id": template_id},
            )
        return self.spawn(
            name=name or f"{tmpl.name} instance",
            owner_id=owner_id,
            description=tmpl.description,
            template_id=template_id,
            metadata={"template_config": tmpl.config},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _transition(
        self,
        env_id: str,
        target: EnvironmentState,
        *,
        actor_id: str,
    ) -> Environment:
        """Validate and execute a state transition."""
        env = self.get_or_raise(env_id)
        before = env.snapshot()

        if not env.can_transition_to(target):
            raise EnvironmentStateError(
                f"Cannot transition from '{env.state.value}' to '{target.value}'",
                context={
                    "environment_id": env_id,
                    "current_state": env.state.value,
                    "target_state": target.value,
                },
            )

        env.state = target
        stmt = (
            sa.update(environments)
            .where(environments.c.id == env_id)
            .values(state=target.value)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        # Map target state to specific action type
        action_map = {
            EnvironmentState.ACTIVE: ActionType.ENV_RESUME,
            EnvironmentState.SUSPENDED: ActionType.ENV_SUSPEND,
            EnvironmentState.COMPLETED: ActionType.ENV_COMPLETE,
            EnvironmentState.DISCARDED: ActionType.ENV_DISCARD,
            EnvironmentState.PROMOTED: ActionType.ENV_PROMOTE,
        }
        action = action_map.get(target, ActionType.LIFECYCLE_CHANGE)

        self._trace(
            actor_id=actor_id,
            action=action,
            env=env,
            before_state=before,
            after_state=env.snapshot(),
        )

        return env

    def _trace(
        self,
        *,
        actor_id: str,
        action: ActionType,
        env: Environment,
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
    ) -> None:
        if self._audit is not None:
            self._audit.record(
                actor_id=actor_id,
                action=action,
                target_type="environment",
                target_id=env.id,
                scope_id=env.scope_id,
                before_state=before_state,
                after_state=after_state,
            )
