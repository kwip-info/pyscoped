"""Environment isolation container.

Tracks which objects belong to an environment and their origin
(created inside vs. projected from outside).  Enforces that only
active environments can accept new objects.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from scoped.exceptions import EnvironmentStateError
from scoped.storage._query import compile_for
from scoped.storage._schema import environment_objects, environments
from scoped.storage.interface import StorageBackend
from scoped.types import generate_id, now_utc

from scoped.environments.models import (
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

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    # ------------------------------------------------------------------
    # Add objects
    # ------------------------------------------------------------------

    def add_object(
        self,
        env_id: str,
        object_id: str,
        *,
        origin: ObjectOrigin = ObjectOrigin.CREATED,
    ) -> EnvironmentObject:
        """Track an object in the environment.

        Raises :class:`EnvironmentStateError` if the environment is
        not in an ACTIVE state.
        """
        self._require_active(env_id)
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
        return eo

    def project_in(self, env_id: str, object_id: str) -> EnvironmentObject:
        """Project an external object into the environment (read-only reference)."""
        return self.add_object(env_id, object_id, origin=ObjectOrigin.PROJECTED)

    # ------------------------------------------------------------------
    # Remove objects
    # ------------------------------------------------------------------

    def remove_object(self, env_id: str, object_id: str) -> bool:
        """Remove an object from the environment.

        Returns ``True`` if the object was removed, ``False`` if it
        was not in the environment.
        """
        stmt = (
            sa.select(environment_objects.c.id)
            .where(
                environment_objects.c.environment_id == env_id,
                environment_objects.c.object_id == object_id,
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            return False
        stmt = (
            sa.delete(environment_objects)
            .where(
                environment_objects.c.environment_id == env_id,
                environment_objects.c.object_id == object_id,
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
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

    def _require_active(self, env_id: str) -> None:
        """Raise if environment is not active."""
        stmt = sa.select(environments.c.state).where(environments.c.id == env_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            from scoped.exceptions import EnvironmentNotFoundError
            raise EnvironmentNotFoundError(
                f"Environment '{env_id}' not found",
                context={"environment_id": env_id},
            )
        state = EnvironmentState(row["state"])
        if state != EnvironmentState.ACTIVE:
            raise EnvironmentStateError(
                f"Environment is '{state.value}', must be 'active' to add objects",
                context={"environment_id": env_id, "state": state.value},
            )
