"""Environment snapshot — capture and restore full state.

A snapshot serializes the complete environment state: the environment
record, all objects within it, their versions, and membership info.
Snapshots never include plaintext secret values.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from scoped.storage._query import compile_for
from scoped.storage._schema import (
    environment_objects,
    environment_snapshots,
    environments,
    object_versions,
    scope_memberships,
    scoped_objects,
)
from scoped.storage.interface import StorageBackend
from scoped.types import generate_id, now_utc

from scoped.environments.models import (
    EnvironmentSnapshot,
    compute_snapshot_checksum,
    snapshot_from_row,
)
from scoped._stability import experimental


@experimental()
class SnapshotManager:
    """Capture and restore environment state snapshots."""

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def capture(
        self,
        env_id: str,
        *,
        created_by: str,
        name: str = "",
    ) -> EnvironmentSnapshot:
        """Capture a full snapshot of the environment's current state.

        Collects the environment record, its objects, and their
        current versions into a single serialized snapshot.
        """
        ts = now_utc()
        snap_id = generate_id()

        data = self._collect_state(env_id)
        checksum = compute_snapshot_checksum(data)

        snap = EnvironmentSnapshot(
            id=snap_id,
            environment_id=env_id,
            name=name,
            snapshot_data=data,
            created_at=ts,
            created_by=created_by,
            checksum=checksum,
        )

        stmt = sa.insert(environment_snapshots).values(
            id=snap.id, environment_id=snap.environment_id,
            name=snap.name, snapshot_data=json.dumps(data, default=str),
            created_at=snap.created_at.isoformat(),
            created_by=snap.created_by, checksum=snap.checksum,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
        return snap

    def get(self, snapshot_id: str) -> EnvironmentSnapshot | None:
        """Fetch a snapshot by ID."""
        stmt = sa.select(environment_snapshots).where(environment_snapshots.c.id == snapshot_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return snapshot_from_row(row) if row else None

    def list_snapshots(
        self,
        env_id: str,
        *,
        limit: int = 100,
    ) -> list[EnvironmentSnapshot]:
        """List snapshots for an environment, newest first."""
        stmt = (
            sa.select(environment_snapshots)
            .where(environment_snapshots.c.environment_id == env_id)
            .order_by(environment_snapshots.c.created_at.desc())
            .limit(limit)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [snapshot_from_row(r) for r in rows]

    def verify(self, snapshot_id: str) -> bool:
        """Verify a snapshot's checksum matches its data."""
        snap = self.get(snapshot_id)
        if snap is None:
            return False
        expected = compute_snapshot_checksum(snap.snapshot_data)
        return snap.checksum == expected

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _collect_state(self, env_id: str) -> dict[str, Any]:
        """Collect the full environment state for serialization."""
        # Environment record
        stmt = sa.select(environments).where(environments.c.id == env_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        env_row = self._backend.fetch_one(sql, params)
        if env_row is None:
            return {"environment": None, "objects": [], "versions": []}

        # Environment objects
        stmt = sa.select(environment_objects).where(environment_objects.c.environment_id == env_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        obj_rows = self._backend.fetch_all(sql, params)

        # Collect current versions for each object
        versions: list[dict[str, Any]] = []
        for obj_row in obj_rows:
            oid = obj_row["object_id"]
            stmt = (
                sa.select(object_versions)
                .select_from(
                    object_versions.join(
                        scoped_objects,
                        (scoped_objects.c.id == object_versions.c.object_id)
                        & (scoped_objects.c.current_version == object_versions.c.version),
                    )
                )
                .where(object_versions.c.object_id == oid)
            )
            sql, params = compile_for(stmt, self._backend.dialect)
            ver_row = self._backend.fetch_one(sql, params)
            if ver_row:
                versions.append(dict(ver_row))

        # Scope memberships
        scope_id = env_row.get("scope_id")
        memberships: list[dict[str, Any]] = []
        if scope_id:
            stmt = (
                sa.select(scope_memberships)
                .where(
                    scope_memberships.c.scope_id == scope_id,
                    scope_memberships.c.lifecycle == "ACTIVE",
                )
            )
            sql, params = compile_for(stmt, self._backend.dialect)
            mem_rows = self._backend.fetch_all(sql, params)
            memberships = [dict(r) for r in mem_rows]

        return {
            "environment": dict(env_row),
            "objects": [dict(r) for r in obj_rows],
            "versions": versions,
            "memberships": memberships,
        }
