"""Environment isolation container.

Tracks which objects belong to an environment and their origin
(created inside vs. projected from outside).  Enforces that only
active environments can accept new objects.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from scoped.exceptions import AccessDeniedError, EnvironmentStateError
from scoped.storage._query import compile_for
from scoped.storage._schema import environment_objects, environments
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, generate_id, now_utc

from scoped.environments.models import (
    Environment,
    EnvironmentObject,
    EnvironmentState,
    ObjectOrigin,
    env_object_from_row,
    environment_from_row,
)
from scoped._stability import experimental


@experimental()
class EnvironmentContainer:
    """Manages the set of objects within an environment."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer

    # ------------------------------------------------------------------
    # Add objects
    # ------------------------------------------------------------------

    def add_object(
        self,
        env_id: str,
        object_id: str,
        *,
        actor_id: str | None = None,
        origin: ObjectOrigin = ObjectOrigin.CREATED,
    ) -> EnvironmentObject:
        """Track an object in the environment.

        Raises :class:`EnvironmentStateError` if the environment is
        not in an ACTIVE state.
        Raises :class:`AccessDeniedError` if *actor_id* is not the
        environment owner.
        """
        env = self._require_active(env_id)
        if actor_id is not None:
            self._require_owner(env, actor_id)
        ts = now_utc()
        eo_id = generate_id()

        eo = EnvironmentObject(
            id=eo_id,
            environment_id=env_id,
            object_id=object_id,
            origin=origin,
            added_at=ts,
        )

        stmt = sa.insert(environment_objects).values(
            id=eo.id, environment_id=eo.environment_id,
            object_id=eo.object_id, origin=eo.origin.value,
            added_at=eo.added_at.isoformat(),
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit and actor_id:
            self._audit.record(
                actor_id=actor_id,
                action=ActionType.LIFECYCLE_CHANGE,
                target_type="environment_object",
                target_id=eo.id,
                scope_id=env.scope_id,
                after_state=eo.snapshot(),
                metadata={"origin": origin.value},
            )

        return eo

    def project_in(
        self,
        env_id: str,
        object_id: str,
        *,
        actor_id: str | None = None,
    ) -> EnvironmentObject:
        """Project an external object into the environment (read-only reference)."""
        return self.add_object(
            env_id, object_id, actor_id=actor_id, origin=ObjectOrigin.PROJECTED,
        )

    # ------------------------------------------------------------------
    # Remove objects
    # ------------------------------------------------------------------

    def remove_object(
        self,
        env_id: str,
        object_id: str,
        *,
        actor_id: str | None = None,
    ) -> bool:
        """Remove an object from the environment.

        Returns ``True`` if the object was removed, ``False`` if it
        was not in the environment.
        """
        if actor_id is not None:
            env = self._get_env(env_id)
            if env is not None:
                self._require_owner(env, actor_id)

        stmt = (
            sa.select(environment_objects)
            .where(
                environment_objects.c.environment_id == env_id,
                environment_objects.c.object_id == object_id,
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            return False

        before_state = env_object_from_row(row).snapshot()

        del_stmt = (
            sa.delete(environment_objects)
            .where(
                environment_objects.c.environment_id == env_id,
                environment_objects.c.object_id == object_id,
            )
        )
        sql, params = compile_for(del_stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit and actor_id:
            scope_id = None
            env = self._get_env(env_id)
            if env is not None:
                scope_id = env.scope_id
            self._audit.record(
                actor_id=actor_id,
                action=ActionType.LIFECYCLE_CHANGE,
                target_type="environment_object",
                target_id=row["id"],
                scope_id=scope_id,
                before_state=before_state,
            )

        return True

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def list_objects(
        self,
        env_id: str,
        *,
        origin: ObjectOrigin | None = None,
        limit: int = 1000,
    ) -> list[EnvironmentObject]:
        """List objects in an environment, optionally filtered by origin."""
        stmt = (
            sa.select(environment_objects)
            .where(environment_objects.c.environment_id == env_id)
        )
        if origin is not None:
            stmt = stmt.where(environment_objects.c.origin == origin.value)
        stmt = stmt.order_by(environment_objects.c.added_at.asc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [env_object_from_row(r) for r in rows]

    def get_created_object_ids(self, env_id: str) -> list[str]:
        """Get IDs of objects created within the environment (promotion candidates)."""
        stmt = (
            sa.select(environment_objects.c.object_id)
            .where(
                environment_objects.c.environment_id == env_id,
                environment_objects.c.origin == "created",
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [r["object_id"] for r in rows]

    def get_projected_object_ids(self, env_id: str) -> list[str]:
        """Get IDs of objects projected into the environment."""
        stmt = (
            sa.select(environment_objects.c.object_id)
            .where(
                environment_objects.c.environment_id == env_id,
                environment_objects.c.origin == "projected",
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [r["object_id"] for r in rows]

    def contains(self, env_id: str, object_id: str) -> bool:
        """Check whether an object is in the environment."""
        stmt = (
            sa.select(sa.literal(1))
            .select_from(environment_objects)
            .where(
                environment_objects.c.environment_id == env_id,
                environment_objects.c.object_id == object_id,
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return row is not None

    def count(self, env_id: str) -> int:
        """Count objects in the environment."""
        stmt = (
            sa.select(sa.func.count().label("cnt"))
            .select_from(environment_objects)
            .where(environment_objects.c.environment_id == env_id)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_env(self, env_id: str) -> Environment | None:
        stmt = sa.select(environments).where(environments.c.id == env_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return environment_from_row(row) if row else None

    @staticmethod
    def _require_owner(env: Environment, actor_id: str) -> None:
        if env.owner_id != actor_id:
            raise AccessDeniedError(
                f"Principal '{actor_id}' is not the owner of environment '{env.id}'",
                context={
                    "environment_id": env.id,
                    "actor_id": actor_id,
                    "owner_id": env.owner_id,
                },
            )

    def _require_active(self, env_id: str) -> Environment:
        """Raise if environment is not active. Returns the environment."""
        env = self._get_env(env_id)
        if env is None:
            from scoped.exceptions import EnvironmentNotFoundError
            raise EnvironmentNotFoundError(
                f"Environment '{env_id}' not found",
                context={"environment_id": env_id},
            )
        if env.state != EnvironmentState.ACTIVE:
            raise EnvironmentStateError(
                f"Environment is '{env.state.value}', must be 'active' to add objects",
                context={"environment_id": env_id, "state": env.state.value},
            )
        return env
