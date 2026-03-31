"""Notification data models.

Notifications are principal-targeted messages generated from events
or directly by the system. They track read/dismiss state and can be
delivered through multiple channels.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from scoped.types import Lifecycle, generate_id, now_utc


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class NotificationStatus(Enum):
    """Lifecycle of a notification from the recipient's perspective."""

    UNREAD = "unread"
    READ = "read"
    DISMISSED = "dismissed"


class NotificationChannel(Enum):
    """Delivery mechanisms for notifications."""

    IN_APP = "in_app"
    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"
    WEBHOOK = "webhook"


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Notification:
    """A message targeting a specific principal.

    Generated from events via notification rules, or created directly
    by framework operations.
    """

    id: str
    recipient_id: str
    title: str
    body: str
    channel: NotificationChannel
    status: NotificationStatus
    created_at: datetime
    source_event_id: str | None = None
    source_rule_id: str | None = None
    scope_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    read_at: datetime | None = None
    dismissed_at: datetime | None = None
    lifecycle: Lifecycle = Lifecycle.ACTIVE


# ---------------------------------------------------------------------------
# Notification rule
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class NotificationRule:
    """When event X matches pattern Y, notify principal Z.

    Rules map event patterns to notification generation.
    ``event_types`` and ``target_types`` filter which events trigger
    this rule. ``recipient_ids`` specifies who gets notified.
    ``channel`` specifies how.
    """

    id: str
    name: str
    owner_id: str
    event_types: list[str]
    target_types: list[str]
    scope_id: str | None
    recipient_ids: list[str]
    channel: NotificationChannel
    title_template: str
    body_template: str
    created_at: datetime
    lifecycle: Lifecycle = Lifecycle.ACTIVE


# ---------------------------------------------------------------------------
# Notification preference
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class NotificationPreference:
    """Per-principal delivery preferences.

    A principal can enable/disable channels and set quiet hours.
    """

    id: str
    principal_id: str
    channel: NotificationChannel
    enabled: bool
    created_at: datetime
    lifecycle: Lifecycle = Lifecycle.ACTIVE


# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------

def notification_from_row(row: dict[str, Any]) -> Notification:
    return Notification(
        id=row["id"],
        recipient_id=row["recipient_id"],
        title=row["title"],
        body=row["body"],
        channel=NotificationChannel(row["channel"]),
        status=NotificationStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        source_event_id=row.get("source_event_id"),
        source_rule_id=row.get("source_rule_id"),
        scope_id=row.get("scope_id"),
        data=json.loads(row["data_json"]) if row.get("data_json") else {},
        read_at=datetime.fromisoformat(row["read_at"]) if row.get("read_at") else None,
        dismissed_at=datetime.fromisoformat(row["dismissed_at"]) if row.get("dismissed_at") else None,
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
    )


def rule_from_row(row: dict[str, Any]) -> NotificationRule:
    return NotificationRule(
        id=row["id"],
        name=row["name"],
        owner_id=row["owner_id"],
        event_types=json.loads(row["event_types_json"]) if row.get("event_types_json") else [],
        target_types=json.loads(row["target_types_json"]) if row.get("target_types_json") else [],
        scope_id=row.get("scope_id"),
        recipient_ids=json.loads(row["recipient_ids_json"]) if row.get("recipient_ids_json") else [],
        channel=NotificationChannel(row["channel"]),
        title_template=row["title_template"],
        body_template=row["body_template"],
        created_at=datetime.fromisoformat(row["created_at"]),
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
    )


def preference_from_row(row: dict[str, Any]) -> NotificationPreference:
    return NotificationPreference(
        id=row["id"],
        principal_id=row["principal_id"],
        channel=NotificationChannel(row["channel"]),
        enabled=bool(row["enabled"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
    )
