"""Environment snapshot — capture and restore full state.

A snapshot serializes the complete environment state: the environment
record, all objects within it, their versions, and membership info.
Snapshots never include plaintext secret values.
"""

from __future__ import annotations

import json
from typing import Any

from scoped.storage.interface import StorageBackend
from scoped.types import generate_id, now_utc

from scoped.environments.models import (
    EnvironmentSnapshot,
    compute_snapshot_checksum,
    snapshot_from_row,
)


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

        self._backend.execute(
            """INSERT INTO environment_snapshots
               (id, environment_id, name, snapshot_data, created_at, created_by, checksum)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                snap.id, snap.environment_id, snap.name,
                json.dumps(data, default=str),
                snap.created_at.isoformat(),
                snap.created_by, snap.checksum,
            ),
        )
        return snap

    def get(self, snapshot_id: str) -> EnvironmentSnapshot | None:
        """Fetch a snapshot by ID."""
        row = self._backend.fetch_one(
            "SELECT * FROM environment_snapshots WHERE id = ?",
            (snapshot_id,),
        )
        return snapshot_from_row(row) if row else None

    def list_snapshots(
        self,
        env_id: str,
        *,
        limit: int = 100,
    ) -> list[EnvironmentSnapshot]:
        """List snapshots for an environment, newest first."""
        rows = self._backend.fetch_all(
            "SELECT * FROM environment_snapshots "
            "WHERE environment_id = ? ORDER BY created_at DESC LIMIT ?",
            (env_id, limit),
        )
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
        env_row = self._backend.fetch_one(
            "SELECT * FROM environments WHERE id = ?", (env_id,),
        )
        if env_row is None:
            return {"environment": None, "objects": [], "versions": []}

        # Environment objects
        obj_rows = self._backend.fetch_all(
            "SELECT * FROM environment_objects WHERE environment_id = ?",
            (env_id,),
        )

        # Collect current versions for each object
        versions: list[dict[str, Any]] = []
        for obj_row in obj_rows:
            oid = obj_row["object_id"]
            ver_row = self._backend.fetch_one(
                """SELECT ov.* FROM object_versions ov
                   JOIN scoped_objects so ON so.id = ov.object_id
                     AND so.current_version = ov.version
                   WHERE ov.object_id = ?""",
                (oid,),
            )
            if ver_row:
                versions.append(dict(ver_row))

        # Scope memberships
        scope_id = env_row.get("scope_id")
        memberships: list[dict[str, Any]] = []
        if scope_id:
            mem_rows = self._backend.fetch_all(
                "SELECT * FROM scope_memberships WHERE scope_id = ? AND lifecycle = 'ACTIVE'",
                (scope_id,),
            )
            memberships = [dict(r) for r in mem_rows]

        return {
            "environment": dict(env_row),
            "objects": [dict(r) for r in obj_rows],
            "versions": versions,
            "memberships": memberships,
        }
