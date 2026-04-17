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
    AccessDeniedError,
    EnvironmentNotFoundError,
    EnvironmentStateError,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import (
    environment_objects,
    environment_templates,
    environments,
    scoped_objects,
)
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


@experimental()
class EnvironmentLifecycle:
    """Manages environment creation and state transitions."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
        container: Any | None = None,
        promotion_manager: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer
        self._scope_lifecycle = ScopeLifecycle(backend, audit_writer=audit_writer)
        self._container = container
        self._promotion_manager = promotion_manager

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
        _validate_json_dict(meta, "metadata")

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
        try:
            self._backend.execute(sql, params)
        except Exception:
            # Clean up orphaned scope if environment insert fails
            self._scope_lifecycle.archive_scope(scope.id, archived_by=owner_id)
            raise

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
        """Discard the environment — archive its scope, tombstone created objects.

        Valid from COMPLETED or PROMOTED states.

        Objects that were *created* inside the environment are
        tombstoned (``lifecycle='ARCHIVED'``).  Projected objects are
        left untouched since they belong to other contexts.
        """
        # Read current state before transition so we can revert on failure
        prev_env = self.get_or_raise(env_id)
        prev_state = prev_env.state

        env = self._transition(env_id, EnvironmentState.DISCARDED, actor_id=actor_id)

        try:
            # Tombstone objects that were created inside this environment
            self._tombstone_created_objects(env_id)

            # Archive the environment's scope (which archives memberships + projections)
            if env.scope_id:
                self._scope_lifecycle.archive_scope(env.scope_id, archived_by=actor_id)
        except Exception:
            # Revert state transition on failure
            stmt = (
                sa.update(environments)
                .where(environments.c.id == env_id)
                .values(state=prev_state.value)
            )
            sql, params = compile_for(stmt, self._backend.dialect)
            self._backend.execute(sql, params)
            raise

        return env

    def promote(
        self,
        env_id: str,
        *,
        actor_id: str,
        target_scope_id: str | None = None,
        target_stage_id: str | None = None,
        access_level: Any | None = None,
    ) -> Environment:
        """Mark environment as promoted (results have been kept).

        Valid from COMPLETED state.  After promotion, the environment
        can still be discarded.

        If *target_scope_id* is provided and the lifecycle was constructed
        with both a container and a promotion_manager, every object
        tracked with origin=CREATED is promoted into the target scope,
        producing real scope projections (Layer 4).  Objects added with
        origin=PROJECTED are *not* auto-promoted — they already live
        elsewhere and were only borrowed into the env.

        Raises whatever :meth:`PromotionManager.promote` raises if a
        per-object promotion fails.  The env state flip to ``PROMOTED``
        happens only after all promotions succeed — a failure leaves the
        env in ``COMPLETED`` so the caller can retry or discard.
        """
        if target_scope_id is not None:
            if self._container is None or self._promotion_manager is None:
                raise EnvironmentStateError(
                    "target_scope_id requires EnvironmentLifecycle to be "
                    "constructed with both a container and a promotion_manager.",
                    context={
                        "environment_id": env_id,
                        "target_scope_id": target_scope_id,
                    },
                )
            env = self.get_or_raise(env_id)
            self._require_owner(env, actor_id)

            created_ids = self._container.get_created_object_ids(env_id)
            kwargs: dict[str, Any] = {}
            if access_level is not None:
                kwargs["access_level"] = access_level
            for object_id in created_ids:
                self._promotion_manager.promote(
                    object_id=object_id,
                    source_env_id=env_id,
                    target_scope_id=target_scope_id,
                    promoted_by=actor_id,
                    target_stage_id=target_stage_id,
                    **kwargs,
                )

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
        _validate_json_dict(cfg, "config")

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

    def _tombstone_created_objects(self, env_id: str) -> None:
        """Archive all objects that were created inside the environment."""
        stmt = (
            sa.select(environment_objects.c.object_id)
            .where(
                environment_objects.c.environment_id == env_id,
                environment_objects.c.origin == "created",
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)

        for row in rows:
            upd = (
                sa.update(scoped_objects)
                .where(
                    scoped_objects.c.id == row["object_id"],
                    scoped_objects.c.lifecycle != "ARCHIVED",
                )
                .values(lifecycle="ARCHIVED")
            )
            sql, params = compile_for(upd, self._backend.dialect)
            self._backend.execute(sql, params)

    def _require_owner(self, env: Environment, actor_id: str) -> None:
        """Raise if the actor is not the environment owner."""
        if env.owner_id != actor_id:
            raise AccessDeniedError(
                f"Principal '{actor_id}' is not the owner of environment '{env.id}'",
                context={
                    "environment_id": env.id,
                    "actor_id": actor_id,
                    "owner_id": env.owner_id,
                },
            )

    def _transition(
        self,
        env_id: str,
        target: EnvironmentState,
        *,
        actor_id: str,
    ) -> Environment:
        """Validate and execute a state transition."""
        env = self.get_or_raise(env_id)
        self._require_owner(env, actor_id)
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
