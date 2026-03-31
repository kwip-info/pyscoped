"""Delivery manager — route notifications to channels and track status."""

from __future__ import annotations

from typing import Any

from scoped.notifications.models import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    notification_from_row,
)
from scoped.storage.interface import StorageBackend
from scoped.types import now_utc


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
        clauses = ["status = 'unread'", "lifecycle = 'ACTIVE'"]
        params: list[Any] = []
        if channel is not None:
            clauses.append("channel = ?")
            params.append(channel.value)
        where = " AND ".join(clauses)
        rows = self._backend.fetch_all(
            f"SELECT * FROM notifications WHERE {where} ORDER BY created_at ASC LIMIT ?",
            tuple(params) + (limit,),
        )
        return [notification_from_row(r) for r in rows]

    def mark_delivered(self, notification_id: str) -> None:
        """Mark a notification as successfully delivered (status → read)."""
        self._backend.execute(
            "UPDATE notifications SET status = 'read', read_at = ? WHERE id = ?",
            (now_utc().isoformat(), notification_id),
        )

    def count_pending(
        self,
        *,
        channel: NotificationChannel | None = None,
    ) -> int:
        """Count notifications pending delivery."""
        clauses = ["status = 'unread'", "lifecycle = 'ACTIVE'"]
        params: list[Any] = []
        if channel is not None:
            clauses.append("channel = ?")
            params.append(channel.value)
        where = " AND ".join(clauses)
        row = self._backend.fetch_one(
            f"SELECT COUNT(*) as cnt FROM notifications WHERE {where}",
            tuple(params),
        )
        return row["cnt"] if row else 0
