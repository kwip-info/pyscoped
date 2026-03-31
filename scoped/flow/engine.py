"""Flow resolution engine.

Manages flow channels and determines whether objects can flow from
one point to another.  Channels are explicit, directional pipes —
information doesn't leak; it travels through defined channels.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from scoped.exceptions import FlowBlockedError
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc

from scoped.flow.models import (
    FlowChannel,
    FlowPointType,
    channel_from_row,
)


@dataclass(frozen=True, slots=True)
class FlowResolution:
    """Result of checking whether a flow is permitted."""

    allowed: bool
    channel: FlowChannel | None = None
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


class FlowEngine:
    """Manages flow channels and resolves flow permissions."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer

    # ------------------------------------------------------------------
    # Channel CRUD
    # ------------------------------------------------------------------

    def create_channel(
        self,
        *,
        name: str,
        source_type: FlowPointType,
        source_id: str,
        target_type: FlowPointType,
        target_id: str,
        owner_id: str,
        allowed_types: list[str] | None = None,
    ) -> FlowChannel:
        """Create a new flow channel."""
        ts = now_utc()
        cid = generate_id()
        types = allowed_types or []

        channel = FlowChannel(
            id=cid, name=name,
            source_type=source_type, source_id=source_id,
            target_type=target_type, target_id=target_id,
            allowed_types=types, owner_id=owner_id,
            created_at=ts,
        )

        self._backend.execute(
            """INSERT INTO flow_channels
               (id, name, source_type, source_id, target_type, target_id,
                allowed_types, owner_id, created_at, lifecycle)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cid, name, source_type.value, source_id,
                target_type.value, target_id,
                json.dumps(types), owner_id, ts.isoformat(), "ACTIVE",
            ),
        )

        if self._audit is not None:
            self._audit.record(
                actor_id=owner_id,
                action=ActionType.CREATE,
                target_type="flow_channel",
                target_id=cid,
                after_state={"name": name, "source_id": source_id, "target_id": target_id},
            )

        return channel

    def get_channel(self, channel_id: str) -> FlowChannel | None:
        row = self._backend.fetch_one(
            "SELECT * FROM flow_channels WHERE id = ?", (channel_id,),
        )
        return channel_from_row(row) if row else None

    def list_channels(
        self,
        *,
        source_type: FlowPointType | None = None,
        source_id: str | None = None,
        target_type: FlowPointType | None = None,
        target_id: str | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[FlowChannel]:
        clauses: list[str] = []
        params: list[Any] = []

        if source_type is not None:
            clauses.append("source_type = ?")
            params.append(source_type.value)
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        if target_type is not None:
            clauses.append("target_type = ?")
            params.append(target_type.value)
        if target_id is not None:
            clauses.append("target_id = ?")
            params.append(target_id)
        if active_only:
            clauses.append("lifecycle = 'ACTIVE'")

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)

        rows = self._backend.fetch_all(
            f"SELECT * FROM flow_channels{where} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        return [channel_from_row(r) for r in rows]

    def archive_channel(self, channel_id: str, *, archived_by: str | None = None) -> None:
        self._backend.execute(
            "UPDATE flow_channels SET lifecycle = 'ARCHIVED' WHERE id = ?",
            (channel_id,),
        )

        if self._audit is not None and archived_by is not None:
            self._audit.record(
                actor_id=archived_by,
                action=ActionType.LIFECYCLE_CHANGE,
                target_type="flow_channel",
                target_id=channel_id,
                before_state={"lifecycle": "ACTIVE"},
                after_state={"lifecycle": "ARCHIVED"},
            )

    # ------------------------------------------------------------------
    # Flow resolution
    # ------------------------------------------------------------------

    def can_flow(
        self,
        *,
        source_type: FlowPointType,
        source_id: str,
        target_type: FlowPointType,
        target_id: str,
        object_type: str | None = None,
    ) -> FlowResolution:
        """Check whether an object can flow from source to target.

        Looks for an active channel matching source→target.  If
        *object_type* is provided, also checks the channel's
        ``allowed_types`` filter.
        """
        channels = self.list_channels(
            source_type=source_type, source_id=source_id,
            target_type=target_type, target_id=target_id,
        )

        if not channels:
            return FlowResolution(
                allowed=False,
                reason="No active flow channel found for this route",
            )

        # Find a channel that permits this object type
        for ch in channels:
            if object_type is None or ch.allows_type(object_type):
                return FlowResolution(allowed=True, channel=ch)

        return FlowResolution(
            allowed=False,
            reason=f"No channel permits object type '{object_type}'",
        )

    def can_flow_or_raise(
        self,
        *,
        source_type: FlowPointType,
        source_id: str,
        target_type: FlowPointType,
        target_id: str,
        object_type: str | None = None,
    ) -> FlowResolution:
        """Like :meth:`can_flow` but raises :class:`FlowBlockedError`."""
        result = self.can_flow(
            source_type=source_type, source_id=source_id,
            target_type=target_type, target_id=target_id,
            object_type=object_type,
        )
        if not result.allowed:
            raise FlowBlockedError(
                result.reason,
                context={
                    "source_type": source_type.value,
                    "source_id": source_id,
                    "target_type": target_type.value,
                    "target_id": target_id,
                    "object_type": object_type,
                },
            )
        return result

    def find_routes(
        self,
        *,
        source_type: FlowPointType,
        source_id: str,
        object_type: str | None = None,
    ) -> list[FlowChannel]:
        """Find all active channels from a given source.

        Optionally filter by object type compatibility.
        """
        channels = self.list_channels(
            source_type=source_type, source_id=source_id,
        )
        if object_type is not None:
            channels = [ch for ch in channels if ch.allows_type(object_type)]
        return channels
