"""Point-in-time state reconstruction.

Given a target (type + ID) and a timestamp, reconstructs the state as
it existed at that moment by querying the audit trail for the most
recent ``after_state`` on or before the timestamp.

Works for any traced entity: objects, scopes, rules, memberships, etc.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from scoped.storage._query import compile_for
from scoped.storage._schema import audit_trail
from scoped.storage.interface import StorageBackend


@dataclass(frozen=True, slots=True)
class ReconstructedState:
    """The result of a point-in-time reconstruction."""

    target_type: str
    target_id: str
    timestamp: datetime
    state: dict[str, Any] | None
    trace_id: str | None
    """The audit trail entry ID that produced this state."""
    found: bool
    """Whether any trace existed for the target at or before the timestamp."""

    def snapshot(self) -> dict[str, Any]:
        return {
            "target_type": self.target_type,
            "target_id": self.target_id,
            "timestamp": self.timestamp.isoformat(),
            "state": self.state,
            "trace_id": self.trace_id,
            "found": self.found,
        }


class StateReconstructor:
    """Rebuild any entity's state at a given point in time.

    Uses the audit trail's ``after_state`` snapshots.  The most recent
    ``after_state`` on or before the requested timestamp is the
    reconstructed state.
    """

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def reconstruct(
        self,
        target_type: str,
        target_id: str,
        at: datetime,
    ) -> ReconstructedState:
        """Reconstruct state of *target* at timestamp *at*.

        Returns a :class:`ReconstructedState` whose ``found`` flag
        indicates whether any trace existed.  If the target had no
        traces on or before *at*, ``state`` is ``None``.
        """
        stmt = (
            sa.select(audit_trail.c.id, audit_trail.c.after_state)
            .where(
                audit_trail.c.target_type == target_type,
                audit_trail.c.target_id == target_id,
                audit_trail.c.timestamp <= at.isoformat(),
                audit_trail.c.after_state.isnot(None),
            )
            .order_by(audit_trail.c.sequence.desc())
            .limit(1)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)

        if row is None:
            return ReconstructedState(
                target_type=target_type,
                target_id=target_id,
                timestamp=at,
                state=None,
                trace_id=None,
                found=False,
            )

        import json
        state = row["after_state"]
        if isinstance(state, str):
            state = json.loads(state)

        return ReconstructedState(
            target_type=target_type,
            target_id=target_id,
            timestamp=at,
            state=state,
            trace_id=row["id"],
            found=True,
        )

    def reconstruct_many(
        self,
        targets: list[tuple[str, str]],
        at: datetime,
    ) -> list[ReconstructedState]:
        """Reconstruct multiple targets at the same timestamp.

        Each element of *targets* is ``(target_type, target_id)``.
        Returns results in the same order as input.
        """
        return [
            self.reconstruct(target_type, target_id, at)
            for target_type, target_id in targets
        ]

    def history_at(
        self,
        target_type: str,
        target_id: str,
        timestamps: list[datetime],
    ) -> list[ReconstructedState]:
        """Reconstruct a target at multiple points in time.

        Useful for building a timeline of state changes.
        Returns results in the same order as input timestamps.
        """
        return [
            self.reconstruct(target_type, target_id, at)
            for at in timestamps
        ]
