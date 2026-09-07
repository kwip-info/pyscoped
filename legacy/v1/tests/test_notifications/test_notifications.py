"""Tests for Layer 15: Notifications."""

from __future__ import annotations

import pytest

from scoped.events.bus import EventBus
from scoped.events.models import Event, EventType
from scoped.notifications.delivery import DeliveryManager
from scoped.notifications.engine import NotificationEngine
from scoped.notifications.models import (
    NotificationChannel,
    NotificationStatus,
    notification_from_row,
    preference_from_row,
    rule_from_row,
)
from scoped.notifications.preferences import PreferenceManager
from scoped.types import Lifecycle, generate_id, now_utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_principal(backend) -> str:
    pid = generate_id()
    ts = now_utc().isoformat()
    backend.execute(
        "INSERT OR IGNORE INTO registry_entries "
        "(id, urn, kind, namespace, name, registered_at, registered_by) "
        "VALUES ('reg_stub', 'scoped:MODEL:test:stub:1', 'MODEL', 'test', 'stub', ?, 'system')",
        (ts,),
    )
    backend.execute(
        "INSERT INTO principals (id, kind, display_name, registry_entry_id, created_at) "
        "VALUES (?, 'user', 'Test User', 'reg_stub', ?)",
        (pid, ts),
    )
    return pid


# ===========================================================================
# Row mappers
# ===========================================================================

class TestRowMappers:
    def test_notification_from_row(self):
        ts = now_utc()
        row = {
            "id": "n1", "recipient_id": "u1", "title": "Hello",
            "body": "World", "channel": "in_app", "status": "unread",
            "created_at": ts.isoformat(), "source_event_id": "e1",
            "source_rule_id": "r1", "scope_id": "s1",
            "data_json": '{"key": "val"}', "read_at": None,
            "dismissed_at": None, "lifecycle": "ACTIVE",
        }
        n = notification_from_row(row)
        assert n.id == "n1"
        assert n.channel == NotificationChannel.IN_APP
        assert n.status == NotificationStatus.UNREAD
        assert n.data == {"key": "val"}

    def test_rule_from_row(self):
        ts = now_utc()
        row = {
            "id": "r1", "name": "test rule", "owner_id": "u1",
            "event_types_json": '["object_created"]',
            "target_types_json": '["document"]',
            "scope_id": None, "recipient_ids_json": '["u2"]',
            "channel": "email", "title_template": "{event_type}",
            "body_template": "{target_type}", "created_at": ts.isoformat(),
            "lifecycle": "ACTIVE",
        }
        r = rule_from_row(row)
        assert r.name == "test rule"
        assert r.event_types == ["object_created"]
        assert r.recipient_ids == ["u2"]
        assert r.channel == NotificationChannel.EMAIL

    def test_preference_from_row(self):
        ts = now_utc()
        row = {
            "id": "p1", "principal_id": "u1", "channel": "push",
            "enabled": 0, "created_at": ts.isoformat(), "lifecycle": "ACTIVE",
        }
        p = preference_from_row(row)
        assert p.principal_id == "u1"
        assert p.channel == NotificationChannel.PUSH
        assert p.enabled is False


# ===========================================================================
# Notification engine — rules
# ===========================================================================

class TestNotificationRules:
    def test_create_rule(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)

        rule = engine.create_rule(
            name="doc creates", owner_id=user,
            event_types=["object_created"],
            recipient_ids=[user],
        )

        assert rule.name == "doc creates"
        assert rule.event_types == ["object_created"]
        assert rule.recipient_ids == [user]

    def test_get_rule(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)

        rule = engine.create_rule(name="test", owner_id=user, recipient_ids=[user])
        fetched = engine.get_rule(rule.id)

        assert fetched is not None
        assert fetched.id == rule.id

    def test_list_rules(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)

        engine.create_rule(name="a", owner_id=user, recipient_ids=[user])
        engine.create_rule(name="b", owner_id=user, recipient_ids=[user])

        rules = engine.list_rules(owner_id=user)
        assert len(rules) == 2

    def test_archive_rule(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)

        rule = engine.create_rule(name="test", owner_id=user, recipient_ids=[user])
        engine.archive_rule(rule.id)

        rules = engine.list_rules(owner_id=user)
        assert len(rules) == 0


# ===========================================================================
# Notification engine — process events
# ===========================================================================

class TestProcessEvent:
    def test_process_creates_notification(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        recipient = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)

        engine.create_rule(
            name="notify on create", owner_id=user,
            event_types=["object_created"],
            recipient_ids=[recipient],
            title_template="New {target_type}",
            body_template="{target_type} {target_id} by {actor_id}",
        )

        event = Event(
            id=generate_id(), event_type=EventType.OBJECT_CREATED,
            actor_id=user, target_type="document", target_id="doc1",
            timestamp=now_utc(),
        )

        notifications = engine.process_event(event)

        assert len(notifications) == 1
        assert notifications[0].recipient_id == recipient
        assert notifications[0].title == "New document"
        assert "doc1" in notifications[0].body
        assert notifications[0].status == NotificationStatus.UNREAD

    def test_process_multiple_recipients(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        r1 = _setup_principal(sqlite_backend)
        r2 = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)

        engine.create_rule(
            name="notify all", owner_id=user,
            recipient_ids=[r1, r2],
        )

        event = Event(
            id=generate_id(), event_type=EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d1",
            timestamp=now_utc(),
        )

        notifications = engine.process_event(event)
        assert len(notifications) == 2
        recipients = {n.recipient_id for n in notifications}
        assert recipients == {r1, r2}

    def test_process_no_matching_rule(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)

        engine.create_rule(
            name="deletes only", owner_id=user,
            event_types=["object_deleted"],
            recipient_ids=[user],
        )

        event = Event(
            id=generate_id(), event_type=EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d1",
            timestamp=now_utc(),
        )

        notifications = engine.process_event(event)
        assert len(notifications) == 0

    def test_process_scope_filtered(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)

        engine.create_rule(
            name="scope-s1", owner_id=user,
            scope_id="scope1",
            recipient_ids=[user],
        )

        event_match = Event(
            id=generate_id(), event_type=EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d1",
            timestamp=now_utc(), scope_id="scope1",
        )
        event_no_match = Event(
            id=generate_id(), event_type=EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d2",
            timestamp=now_utc(), scope_id="scope2",
        )

        assert len(engine.process_event(event_match)) == 1
        assert len(engine.process_event(event_no_match)) == 0

    def test_process_preserves_event_data(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)

        engine.create_rule(name="all", owner_id=user, recipient_ids=[user])

        event = Event(
            id=generate_id(), event_type=EventType.CUSTOM,
            actor_id=user, target_type="task", target_id="t1",
            timestamp=now_utc(), data={"progress": 100},
        )

        notifications = engine.process_event(event)
        assert notifications[0].data == {"progress": 100}
        assert notifications[0].source_event_id == event.id

    def test_process_with_channel(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)

        engine.create_rule(
            name="email", owner_id=user,
            recipient_ids=[user],
            channel=NotificationChannel.EMAIL,
        )

        event = Event(
            id=generate_id(), event_type=EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d1",
            timestamp=now_utc(),
        )

        notifications = engine.process_event(event)
        assert notifications[0].channel == NotificationChannel.EMAIL


# ===========================================================================
# Notification CRUD
# ===========================================================================

class TestNotificationCRUD:
    def test_list_notifications(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)

        engine.create_rule(name="all", owner_id=user, recipient_ids=[user])

        for i in range(3):
            event = Event(
                id=generate_id(), event_type=EventType.OBJECT_CREATED,
                actor_id=user, target_type="doc", target_id=f"d{i}",
                timestamp=now_utc(),
            )
            engine.process_event(event)

        notifications = engine.list_notifications(recipient_id=user)
        assert len(notifications) == 3

    def test_list_by_status(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)

        engine.create_rule(name="all", owner_id=user, recipient_ids=[user])

        event = Event(
            id=generate_id(), event_type=EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d1",
            timestamp=now_utc(),
        )
        notifications = engine.process_event(event)
        engine.mark_read(notifications[0].id)

        unread = engine.list_notifications(recipient_id=user, status=NotificationStatus.UNREAD)
        assert len(unread) == 0

        read = engine.list_notifications(recipient_id=user, status=NotificationStatus.READ)
        assert len(read) == 1

    def test_mark_read(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)

        engine.create_rule(name="all", owner_id=user, recipient_ids=[user])
        event = Event(
            id=generate_id(), event_type=EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d1",
            timestamp=now_utc(),
        )
        notifications = engine.process_event(event)
        engine.mark_read(notifications[0].id)

        fetched = engine.get_notification(notifications[0].id)
        assert fetched.status == NotificationStatus.READ
        assert fetched.read_at is not None

    def test_mark_dismissed(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)

        engine.create_rule(name="all", owner_id=user, recipient_ids=[user])
        event = Event(
            id=generate_id(), event_type=EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d1",
            timestamp=now_utc(),
        )
        notifications = engine.process_event(event)
        engine.mark_dismissed(notifications[0].id)

        fetched = engine.get_notification(notifications[0].id)
        assert fetched.status == NotificationStatus.DISMISSED
        assert fetched.dismissed_at is not None

    def test_count_unread(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)

        engine.create_rule(name="all", owner_id=user, recipient_ids=[user])

        for i in range(3):
            event = Event(
                id=generate_id(), event_type=EventType.OBJECT_CREATED,
                actor_id=user, target_type="doc", target_id=f"d{i}",
                timestamp=now_utc(),
            )
            engine.process_event(event)

        assert engine.count_unread(user) == 3


# ===========================================================================
# Delivery manager
# ===========================================================================

class TestDeliveryManager:
    def test_get_pending(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)
        delivery = DeliveryManager(sqlite_backend)

        engine.create_rule(name="all", owner_id=user, recipient_ids=[user])
        event = Event(
            id=generate_id(), event_type=EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d1",
            timestamp=now_utc(),
        )
        engine.process_event(event)

        pending = delivery.get_pending()
        assert len(pending) == 1

    def test_get_pending_by_channel(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)
        delivery = DeliveryManager(sqlite_backend)

        engine.create_rule(
            name="in-app", owner_id=user, recipient_ids=[user],
            channel=NotificationChannel.IN_APP,
        )
        engine.create_rule(
            name="email", owner_id=user, recipient_ids=[user],
            channel=NotificationChannel.EMAIL,
        )

        event = Event(
            id=generate_id(), event_type=EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d1",
            timestamp=now_utc(),
        )
        engine.process_event(event)

        in_app = delivery.get_pending(channel=NotificationChannel.IN_APP)
        assert len(in_app) == 1
        email = delivery.get_pending(channel=NotificationChannel.EMAIL)
        assert len(email) == 1

    def test_mark_delivered(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)
        delivery = DeliveryManager(sqlite_backend)

        engine.create_rule(name="all", owner_id=user, recipient_ids=[user])
        event = Event(
            id=generate_id(), event_type=EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d1",
            timestamp=now_utc(),
        )
        notifications = engine.process_event(event)
        delivery.mark_delivered(notifications[0].id)

        pending = delivery.get_pending()
        assert len(pending) == 0

    def test_count_pending(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)
        delivery = DeliveryManager(sqlite_backend)

        engine.create_rule(name="all", owner_id=user, recipient_ids=[user])
        for i in range(3):
            event = Event(
                id=generate_id(), event_type=EventType.OBJECT_CREATED,
                actor_id=user, target_type="doc", target_id=f"d{i}",
                timestamp=now_utc(),
            )
            engine.process_event(event)

        assert delivery.count_pending() == 3


# ===========================================================================
# Preferences
# ===========================================================================

class TestPreferences:
    def test_set_preference(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = PreferenceManager(sqlite_backend)

        pref = mgr.set_preference(
            principal_id=user, channel=NotificationChannel.EMAIL, enabled=False,
        )

        assert pref.principal_id == user
        assert pref.channel == NotificationChannel.EMAIL
        assert pref.enabled is False

    def test_default_enabled(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = PreferenceManager(sqlite_backend)

        # No preference set = enabled
        assert mgr.is_channel_enabled(user, NotificationChannel.EMAIL) is True

    def test_disable_channel(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = PreferenceManager(sqlite_backend)

        mgr.set_preference(principal_id=user, channel=NotificationChannel.SMS, enabled=False)
        assert mgr.is_channel_enabled(user, NotificationChannel.SMS) is False

    def test_update_preference(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = PreferenceManager(sqlite_backend)

        mgr.set_preference(principal_id=user, channel=NotificationChannel.PUSH, enabled=False)
        mgr.set_preference(principal_id=user, channel=NotificationChannel.PUSH, enabled=True)

        assert mgr.is_channel_enabled(user, NotificationChannel.PUSH) is True

    def test_get_preferences(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = PreferenceManager(sqlite_backend)

        mgr.set_preference(principal_id=user, channel=NotificationChannel.EMAIL, enabled=True)
        mgr.set_preference(principal_id=user, channel=NotificationChannel.SMS, enabled=False)

        prefs = mgr.get_preferences(user)
        assert len(prefs) == 2

    def test_delete_preference(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = PreferenceManager(sqlite_backend)

        mgr.set_preference(principal_id=user, channel=NotificationChannel.EMAIL, enabled=False)
        mgr.delete_preference(user, NotificationChannel.EMAIL)

        # Reverts to default (enabled)
        assert mgr.is_channel_enabled(user, NotificationChannel.EMAIL) is True
        prefs = mgr.get_preferences(user)
        assert len(prefs) == 0


# ===========================================================================
# Integration: EventBus → NotificationEngine
# ===========================================================================

class TestEventBusIntegration:
    def test_bus_triggers_notifications(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        recipient = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)
        engine = NotificationEngine(sqlite_backend)

        engine.create_rule(
            name="notify on create", owner_id=user,
            event_types=["object_created"],
            recipient_ids=[recipient],
        )

        # Wire bus to engine
        bus.on(EventType.OBJECT_CREATED, lambda e: engine.process_event(e))

        bus.emit(
            EventType.OBJECT_CREATED,
            actor_id=user, target_type="document", target_id="d1",
        )

        notifications = engine.list_notifications(recipient_id=recipient)
        assert len(notifications) == 1
        assert notifications[0].source_event_id is not None
