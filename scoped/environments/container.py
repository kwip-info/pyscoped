"""Environment isolation container.

Tracks which objects belong to an environment and their origin
(created inside vs. projected from outside).  Enforces that only
active environments can accept new objects.
"""

from __future__ import annotations

from typing import Any

from scoped.exceptions import EnvironmentStateError
from scoped.storage.interface import StorageBackend
from scoped.types import generate_id, now_utc

from scoped.environments.models import (
    EnvironmentObject,
    EnvironmentState,
    ObjectOrigin,
    env_object_from_row,
    environment_from_row,
)


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

        self._backend.execute(
            """INSERT INTO environment_objects
               (id, environment_id, object_id, origin, added_at)
               VALUES (?, ?, ?, ?, ?)""",
            (eo.id, eo.environment_id, eo.object_id, eo.origin.value, eo.added_at.isoformat()),
        )
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
        row = self._backend.fetch_one(
            "SELECT id FROM environment_objects WHERE environment_id = ? AND object_id = ?",
            (env_id, object_id),
        )
        if row is None:
            return False
        self._backend.execute(
            "DELETE FROM environment_objects WHERE environment_id = ? AND object_id = ?",
            (env_id, object_id),
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
        if origin is not None:
            rows = self._backend.fetch_all(
                "SELECT * FROM environment_objects "
                "WHERE environment_id = ? AND origin = ? "
                "ORDER BY added_at ASC LIMIT ?",
                (env_id, origin.value, limit),
            )
        else:
            rows = self._backend.fetch_all(
                "SELECT * FROM environment_objects "
                "WHERE environment_id = ? ORDER BY added_at ASC LIMIT ?",
                (env_id, limit),
            )
        return [env_object_from_row(r) for r in rows]

    def get_created_object_ids(self, env_id: str) -> list[str]:
        """Get IDs of objects created within the environment (promotion candidates)."""
        rows = self._backend.fetch_all(
            "SELECT object_id FROM environment_objects "
            "WHERE environment_id = ? AND origin = 'created'",
            (env_id,),
        )
        return [r["object_id"] for r in rows]

    def get_projected_object_ids(self, env_id: str) -> list[str]:
        """Get IDs of objects projected into the environment."""
        rows = self._backend.fetch_all(
            "SELECT object_id FROM environment_objects "
            "WHERE environment_id = ? AND origin = 'projected'",
            (env_id,),
        )
        return [r["object_id"] for r in rows]

    def contains(self, env_id: str, object_id: str) -> bool:
        """Check whether an object is in the environment."""
        row = self._backend.fetch_one(
            "SELECT 1 FROM environment_objects WHERE environment_id = ? AND object_id = ?",
            (env_id, object_id),
        )
        return row is not None

    def count(self, env_id: str) -> int:
        """Count objects in the environment."""
        row = self._backend.fetch_one(
            "SELECT COUNT(*) as cnt FROM environment_objects WHERE environment_id = ?",
            (env_id,),
        )
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _require_active(self, env_id: str) -> None:
        """Raise if environment is not active."""
        row = self._backend.fetch_one(
            "SELECT state FROM environments WHERE id = ?", (env_id,),
        )
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
