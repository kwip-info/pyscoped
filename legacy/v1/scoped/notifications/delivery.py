"""Delivery manager — route notifications to channels and track status."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from scoped.notifications.models import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    notification_from_row,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import notifications
from scoped.storage.interface import StorageBackend
from scoped.types import now_utc
from scoped._stability import experimental


@experimental()
class DeliveryManager:
    """Route notifications to their channels and track delivery.

    For the framework layer, delivery is tracked via status updates.
    Actual channel transport (email, SMS, push) is the application's
    responsibility — the framework provides the routing and tracking.

    Parameters
    ----------
    backend:
        Storage backend for persistence.
    """

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def get_pending(
        self,
        *,
        channel: NotificationChannel | None = None,
        limit: int = 100,
    ) -> list[Notification]:
        """Get unread notifications pending delivery."""
        stmt = sa.select(notifications).where(
            notifications.c.status == "unread",
            notifications.c.lifecycle == "ACTIVE",
        )
        if channel is not None:
            stmt = stmt.where(notifications.c.channel == channel.value)
        stmt = stmt.order_by(notifications.c.created_at.asc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [notification_from_row(r) for r in rows]

    def mark_delivered(self, notification_id: str) -> None:
        """Mark a notification as successfully delivered (status -> read)."""
        stmt = (
            sa.update(notifications)
            .where(notifications.c.id == notification_id)
            .values(status="read", read_at=now_utc().isoformat())
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

    def count_pending(
        self,
        *,
        channel: NotificationChannel | None = None,
    ) -> int:
        """Count notifications pending delivery."""
        stmt = (
            sa.select(sa.func.count().label("cnt"))
            .select_from(notifications)
            .where(
                notifications.c.status == "unread",
                notifications.c.lifecycle == "ACTIVE",
            )
        )
        if channel is not None:
            stmt = stmt.where(notifications.c.channel == channel.value)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return row["cnt"] if row else 0
