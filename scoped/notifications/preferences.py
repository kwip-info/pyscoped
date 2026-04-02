"""Preference manager — per-principal notification delivery preferences."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa

from scoped.notifications.models import (
    NotificationChannel,
    NotificationPreference,
    preference_from_row,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import notification_preferences
from scoped.storage.interface import StorageBackend
from scoped.types import Lifecycle, generate_id, now_utc
from scoped._stability import experimental


@experimental()
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
        stmt = sa.select(notification_preferences).where(
            notification_preferences.c.principal_id == principal_id,
            notification_preferences.c.channel == channel.value,
            notification_preferences.c.lifecycle == "ACTIVE",
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        existing = self._backend.fetch_one(sql, params)
        if existing:
            update_stmt = (
                sa.update(notification_preferences)
                .where(notification_preferences.c.id == existing["id"])
                .values(enabled=int(enabled))
            )
            sql_u, params_u = compile_for(update_stmt, self._backend.dialect)
            self._backend.execute(sql_u, params_u)
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
        insert_stmt = sa.insert(notification_preferences).values(
            id=pref.id,
            principal_id=pref.principal_id,
            channel=pref.channel.value,
            enabled=int(pref.enabled),
            created_at=pref.created_at.isoformat(),
            lifecycle=pref.lifecycle.name,
        )
        sql_i, params_i = compile_for(insert_stmt, self._backend.dialect)
        self._backend.execute(sql_i, params_i)
        return pref

    def get_preferences(self, principal_id: str) -> list[NotificationPreference]:
        """Get all active preferences for a principal."""
        stmt = sa.select(notification_preferences).where(
            notification_preferences.c.principal_id == principal_id,
            notification_preferences.c.lifecycle == "ACTIVE",
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [preference_from_row(r) for r in rows]

    def is_channel_enabled(
        self,
        principal_id: str,
        channel: NotificationChannel,
    ) -> bool:
        """Check if a channel is enabled for a principal.

        Returns True if no preference exists (default = enabled).
        """
        stmt = sa.select(notification_preferences).where(
            notification_preferences.c.principal_id == principal_id,
            notification_preferences.c.channel == channel.value,
            notification_preferences.c.lifecycle == "ACTIVE",
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            return True  # default enabled
        return bool(row["enabled"])

    def delete_preference(
        self,
        principal_id: str,
        channel: NotificationChannel,
    ) -> None:
        """Remove a preference (reverts to default=enabled)."""
        stmt = (
            sa.update(notification_preferences)
            .where(
                notification_preferences.c.principal_id == principal_id,
                notification_preferences.c.channel == channel.value,
                notification_preferences.c.lifecycle == "ACTIVE",
            )
            .values(lifecycle="ARCHIVED")
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
