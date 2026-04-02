"""Preference manager — per-principal notification delivery preferences."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from scoped.notifications.models import (
    NotificationChannel,
    NotificationPreference,
    preference_from_row,
)
from scoped.storage.interface import StorageBackend
from scoped.types import Lifecycle, generate_id, now_utc


class PreferenceManager:
    """Manage per-principal notification channel preferences.

    Preferences control which channels are enabled for a principal.
    By default, all channels are enabled (no preference = enabled).

    Parameters
    ----------
    backend:
        Storage backend for persistence.
    """

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def set_preference(
        self,
        *,
        principal_id: str,
        channel: NotificationChannel,
        enabled: bool,
    ) -> NotificationPreference:
        """Set or update a notification preference for a principal/channel pair."""
        existing = self._backend.fetch_one(
            "SELECT * FROM notification_preferences "
            "WHERE principal_id = ? AND channel = ? AND lifecycle = 'ACTIVE'",
            (principal_id, channel.value),
        )
        if existing:
            self._backend.execute(
                "UPDATE notification_preferences SET enabled = ? WHERE id = ?",
                (int(enabled), existing["id"]),
            )
            return NotificationPreference(
                id=existing["id"],
                principal_id=principal_id,
                channel=channel,
                enabled=enabled,
                created_at=datetime.fromisoformat(existing["created_at"]),
            )

        pref = NotificationPreference(
            id=generate_id(),
            principal_id=principal_id,
            channel=channel,
            enabled=enabled,
            created_at=now_utc(),
        )
        self._backend.execute(
            "INSERT INTO notification_preferences "
            "(id, principal_id, channel, enabled, created_at, lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                pref.id, pref.principal_id, pref.channel.value,
                int(pref.enabled), pref.created_at.isoformat(),
                pref.lifecycle.name,
            ),
        )
        return pref

    def get_preferences(self, principal_id: str) -> list[NotificationPreference]:
        """Get all active preferences for a principal."""
        rows = self._backend.fetch_all(
            "SELECT * FROM notification_preferences "
            "WHERE principal_id = ? AND lifecycle = 'ACTIVE'",
            (principal_id,),
        )
        return [preference_from_row(r) for r in rows]

    def is_channel_enabled(
        self,
        principal_id: str,
        channel: NotificationChannel,
    ) -> bool:
        """Check if a channel is enabled for a principal.

        Returns True if no preference exists (default = enabled).
        """
        row = self._backend.fetch_one(
            "SELECT * FROM notification_preferences "
            "WHERE principal_id = ? AND channel = ? AND lifecycle = 'ACTIVE'",
            (principal_id, channel.value),
        )
        if row is None:
            return True  # default enabled
        return bool(row["enabled"])

    def delete_preference(
        self,
        principal_id: str,
        channel: NotificationChannel,
    ) -> None:
        """Remove a preference (reverts to default=enabled)."""
        self._backend.execute(
            "UPDATE notification_preferences SET lifecycle = 'ARCHIVED' "
            "WHERE principal_id = ? AND channel = ? AND lifecycle = 'ACTIVE'",
            (principal_id, channel.value),
        )
