"""Tests for event models and row mappers."""

from __future__ import annotations

import json

import pytest

from scoped.events.models import (
    DeliveryAttempt,
    DeliveryStatus,
    Event,
    EventSubscription,
    EventType,
    WebhookEndpoint,
    event_from_row,
    subscription_from_row,
    webhook_from_row,
)
from scoped.types import Lifecycle, now_utc


# ===========================================================================
# EventType enum
# ===========================================================================

class TestEventType:
    def test_values(self):
        assert EventType.OBJECT_CREATED.value == "object_created"
        assert EventType.CUSTOM.value == "custom"

    def test_all_types_unique(self):
        values = [e.value for e in EventType]
        assert len(values) == len(set(values))


# ===========================================================================
# DeliveryStatus enum
# ===========================================================================

class TestDeliveryStatus:
    def test_values(self):
        assert DeliveryStatus.PENDING.value == "pending"
        assert DeliveryStatus.DELIVERED.value == "delivered"
        assert DeliveryStatus.FAILED.value == "failed"
        assert DeliveryStatus.RETRYING.value == "retrying"


# ===========================================================================
# Event model
# ===========================================================================

class TestEventModel:
    def test_snapshot(self):
        ts = now_utc()
        event = Event(
            id="e1", event_type=EventType.OBJECT_CREATED,
            actor_id="user1", target_type="document", target_id="doc1",
            timestamp=ts, scope_id="scope1", data={"key": "value"},
            source_trace_id="trace1",
        )
        snap = event.snapshot()
        assert snap["id"] == "e1"
        assert snap["event_type"] == "object_created"
        assert snap["actor_id"] == "user1"
        assert snap["data"] == {"key": "value"}
        assert snap["source_trace_id"] == "trace1"

    def test_frozen(self):
        event = Event(
            id="e1", event_type=EventType.OBJECT_CREATED,
            actor_id="user1", target_type="doc", target_id="d1",
            timestamp=now_utc(),
        )
        with pytest.raises(AttributeError):
            event.id = "changed"

    def test_default_data(self):
        event = Event(
            id="e1", event_type=EventType.CUSTOM,
            actor_id="user1", target_type="x", target_id="y",
            timestamp=now_utc(),
        )
        assert event.data == {}
        assert event.scope_id is None
        assert event.source_trace_id is None


# ===========================================================================
# EventSubscription model
# ===========================================================================

class TestSubscriptionModel:
    def _make_event(self, **kwargs):
        defaults = dict(
            id="e1", event_type=EventType.OBJECT_CREATED,
            actor_id="user1", target_type="document", target_id="doc1",
            timestamp=now_utc(), scope_id="scope1",
        )
        defaults.update(kwargs)
        return Event(**defaults)

    def test_matches_all(self):
        sub = EventSubscription(
            id="s1", name="all", owner_id="u1",
            event_types=[], target_types=[], scope_id=None,
            webhook_endpoint_id=None, created_at=now_utc(),
        )
        assert sub.matches(self._make_event()) is True

    def test_matches_event_type(self):
        sub = EventSubscription(
            id="s1", name="creates", owner_id="u1",
            event_types=["object_created"], target_types=[], scope_id=None,
            webhook_endpoint_id=None, created_at=now_utc(),
        )
        assert sub.matches(self._make_event()) is True
        assert sub.matches(self._make_event(event_type=EventType.OBJECT_DELETED)) is False

    def test_matches_target_type(self):
        sub = EventSubscription(
            id="s1", name="docs", owner_id="u1",
            event_types=[], target_types=["document"], scope_id=None,
            webhook_endpoint_id=None, created_at=now_utc(),
        )
        assert sub.matches(self._make_event(target_type="document")) is True
        assert sub.matches(self._make_event(target_type="note")) is False

    def test_matches_scope(self):
        sub = EventSubscription(
            id="s1", name="scoped", owner_id="u1",
            event_types=[], target_types=[], scope_id="scope1",
            webhook_endpoint_id=None, created_at=now_utc(),
        )
        assert sub.matches(self._make_event(scope_id="scope1")) is True
        assert sub.matches(self._make_event(scope_id="scope2")) is False

    def test_archived_never_matches(self):
        sub = EventSubscription(
            id="s1", name="archived", owner_id="u1",
            event_types=[], target_types=[], scope_id=None,
            webhook_endpoint_id=None, created_at=now_utc(),
            lifecycle=Lifecycle.ARCHIVED,
        )
        assert sub.matches(self._make_event()) is False


# ===========================================================================
# Row mappers
# ===========================================================================

class TestRowMappers:
    def test_event_from_row(self):
        ts = now_utc()
        row = {
            "id": "e1", "event_type": "object_created",
            "actor_id": "user1", "target_type": "doc", "target_id": "d1",
            "timestamp": ts.isoformat(), "scope_id": "s1",
            "data_json": '{"key": "val"}', "source_trace_id": "t1",
            "lifecycle": "ACTIVE",
        }
        event = event_from_row(row)
        assert event.id == "e1"
        assert event.event_type == EventType.OBJECT_CREATED
        assert event.data == {"key": "val"}

    def test_subscription_from_row(self):
        ts = now_utc()
        row = {
            "id": "s1", "name": "test", "owner_id": "u1",
            "event_types_json": '["object_created"]',
            "target_types_json": '["document"]',
            "scope_id": None, "webhook_endpoint_id": "wh1",
            "created_at": ts.isoformat(), "lifecycle": "ACTIVE",
        }
        sub = subscription_from_row(row)
        assert sub.id == "s1"
        assert sub.event_types == ["object_created"]
        assert sub.target_types == ["document"]
        assert sub.webhook_endpoint_id == "wh1"

    def test_webhook_from_row(self):
        ts = now_utc()
        row = {
            "id": "wh1", "name": "my hook", "owner_id": "u1",
            "url": "https://example.com/hook",
            "config_json": '{"timeout": 30}',
            "scope_id": None, "created_at": ts.isoformat(),
            "lifecycle": "ACTIVE",
        }
        wh = webhook_from_row(row)
        assert wh.id == "wh1"
        assert wh.url == "https://example.com/hook"
        assert wh.config == {"timeout": 30}
