"""Event and webhook data models.

Events are typed, scoped occurrences that can be subscribed to and
delivered via webhooks.  They complement the audit trail: audit is
the passive, immutable record; events are the active, reactive signal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any

from scoped.types import ActionType, Lifecycle, generate_id, now_utc


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

class EventType(Enum):
    """Categories of events that the bus can emit."""

    OBJECT_CREATED = "object_created"
    OBJECT_UPDATED = "object_updated"
    OBJECT_DELETED = "object_deleted"
    SCOPE_CREATED = "scope_created"
    SCOPE_MODIFIED = "scope_modified"
    SCOPE_DISSOLVED = "scope_dissolved"
    MEMBERSHIP_CHANGED = "membership_changed"
    RULE_CHANGED = "rule_changed"
    ENVIRONMENT_SPAWNED = "environment_spawned"
    ENVIRONMENT_COMPLETED = "environment_completed"
    ENVIRONMENT_DISCARDED = "environment_discarded"
    ENVIRONMENT_PROMOTED = "environment_promoted"
    DEPLOYMENT_COMPLETED = "deployment_completed"
    DEPLOYMENT_ROLLED_BACK = "deployment_rolled_back"
    SECRET_ROTATED = "secret_rotated"
    STAGE_TRANSITIONED = "stage_transitioned"
    CONNECTOR_SYNCED = "connector_synced"
    CUSTOM = "custom"


# ---------------------------------------------------------------------------
# Delivery status
# ---------------------------------------------------------------------------

class DeliveryStatus(Enum):
    """Status of a webhook delivery attempt."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    RETRYING = "retrying"


# ---------------------------------------------------------------------------
# Core event model
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Event:
    """A typed, scoped occurrence in the system.

    Events are produced by the EventBus in response to framework actions.
    They carry enough context for subscribers to react without needing
    to query additional state.
    """

    id: str
    event_type: EventType
    actor_id: str
    target_type: str
    target_id: str
    timestamp: datetime
    scope_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    source_trace_id: str | None = None
    lifecycle: Lifecycle = Lifecycle.ACTIVE

    def snapshot(self) -> dict[str, Any]:
        """Serializable representation."""
        return {
            "id": self.id,
            "event_type": self.event_type.value,
            "actor_id": self.actor_id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "timestamp": self.timestamp.isoformat(),
            "scope_id": self.scope_id,
            "data": self.data,
            "source_trace_id": self.source_trace_id,
        }


# ---------------------------------------------------------------------------
# Event subscription
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class EventSubscription:
    """A principal's subscription to a pattern of events.

    Filters narrow which events trigger this subscription:
    - ``event_types``: list of EventType values to match (empty = all)
    - ``target_types``: list of target_type strings to match (empty = all)
    - ``scope_id``: restrict to events within this scope (None = any)
    """

    id: str
    name: str
    owner_id: str
    event_types: list[str]
    target_types: list[str]
    scope_id: str | None
    webhook_endpoint_id: str | None
    created_at: datetime
    lifecycle: Lifecycle = Lifecycle.ACTIVE

    def matches(self, event: Event) -> bool:
        """Return True if *event* matches this subscription's filters."""
        if self.lifecycle != Lifecycle.ACTIVE:
            return False
        if self.event_types and event.event_type.value not in self.event_types:
            return False
        if self.target_types and event.target_type not in self.target_types:
            return False
        if self.scope_id is not None and event.scope_id != self.scope_id:
            return False
        return True


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WebhookEndpoint:
    """An outbound HTTP target for event delivery.

    Endpoints are owned by a principal and optionally scoped.
    ``config`` stores URL, auth headers, timeout, retry policy, etc.
    """

    id: str
    name: str
    owner_id: str
    url: str
    config: dict[str, Any]
    scope_id: str | None
    created_at: datetime
    lifecycle: Lifecycle = Lifecycle.ACTIVE


# ---------------------------------------------------------------------------
# Delivery attempt
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DeliveryAttempt:
    """Record of a single webhook delivery attempt."""

    id: str
    event_id: str
    webhook_endpoint_id: str
    subscription_id: str
    status: DeliveryStatus
    attempted_at: datetime
    response_status: int | None = None
    response_body: str | None = None
    error_message: str | None = None
    attempt_number: int = 1


# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------

def event_from_row(row: dict[str, Any]) -> Event:
    """Construct an Event from a database row."""
    return Event(
        id=row["id"],
        event_type=EventType(row["event_type"]),
        actor_id=row["actor_id"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        scope_id=row.get("scope_id"),
        data=json.loads(row["data_json"]) if row.get("data_json") else {},
        source_trace_id=row.get("source_trace_id"),
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
    )


def subscription_from_row(row: dict[str, Any]) -> EventSubscription:
    """Construct an EventSubscription from a database row."""
    return EventSubscription(
        id=row["id"],
        name=row["name"],
        owner_id=row["owner_id"],
        event_types=json.loads(row["event_types_json"]) if row.get("event_types_json") else [],
        target_types=json.loads(row["target_types_json"]) if row.get("target_types_json") else [],
        scope_id=row.get("scope_id"),
        webhook_endpoint_id=row.get("webhook_endpoint_id"),
        created_at=datetime.fromisoformat(row["created_at"]),
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
    )


def webhook_from_row(row: dict[str, Any]) -> WebhookEndpoint:
    """Construct a WebhookEndpoint from a database row."""
    return WebhookEndpoint(
        id=row["id"],
        name=row["name"],
        owner_id=row["owner_id"],
        url=row["url"],
        config=json.loads(row["config_json"]) if row.get("config_json") else {},
        scope_id=row.get("scope_id"),
        created_at=datetime.fromisoformat(row["created_at"]),
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
    )
