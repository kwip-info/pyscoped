"""Tests for EventBus — emit, persist, match, dispatch."""

from __future__ import annotations

import pytest

from scoped.events.bus import EventBus
from scoped.events.models import EventType
from scoped.events.subscriptions import SubscriptionManager
from scoped.types import generate_id, now_utc


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
# Emit
# ===========================================================================

class TestEventEmit:
    def test_emit_persists_event(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)

        event = bus.emit(
            EventType.OBJECT_CREATED,
            actor_id=user,
            target_type="document",
            target_id="doc1",
        )

        assert event.event_type == EventType.OBJECT_CREATED
        assert event.actor_id == user
        fetched = bus.get_event(event.id)
        assert fetched is not None
        assert fetched.id == event.id

    def test_emit_with_scope(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)

        event = bus.emit(
            EventType.SCOPE_CREATED,
            actor_id=user,
            target_type="scope",
            target_id="s1",
            scope_id="s1",
        )

        assert event.scope_id == "s1"

    def test_emit_with_data(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)

        event = bus.emit(
            EventType.CUSTOM,
            actor_id=user,
            target_type="task",
            target_id="t1",
            data={"status": "complete", "score": 95},
        )

        fetched = bus.get_event(event.id)
        assert fetched.data == {"status": "complete", "score": 95}

    def test_emit_with_trace_id(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)

        event = bus.emit(
            EventType.OBJECT_UPDATED,
            actor_id=user,
            target_type="doc",
            target_id="d1",
            source_trace_id="trace-abc",
        )

        assert event.source_trace_id == "trace-abc"


# ===========================================================================
# In-process listeners
# ===========================================================================

class TestListeners:
    def test_listener_called_on_emit(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)
        received = []

        bus.on(EventType.OBJECT_CREATED, lambda e: received.append(e))
        bus.emit(
            EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d1",
        )

        assert len(received) == 1
        assert received[0].event_type == EventType.OBJECT_CREATED

    def test_listener_not_called_for_other_type(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)
        received = []

        bus.on(EventType.OBJECT_DELETED, lambda e: received.append(e))
        bus.emit(
            EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d1",
        )

        assert len(received) == 0

    def test_remove_listener(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)
        received = []

        listener = lambda e: received.append(e)
        bus.on(EventType.OBJECT_CREATED, listener)
        bus.off(EventType.OBJECT_CREATED, listener)
        bus.emit(
            EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d1",
        )

        assert len(received) == 0

    def test_multiple_listeners(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)
        received_a = []
        received_b = []

        bus.on(EventType.OBJECT_CREATED, lambda e: received_a.append(e))
        bus.on(EventType.OBJECT_CREATED, lambda e: received_b.append(e))
        bus.emit(
            EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d1",
        )

        assert len(received_a) == 1
        assert len(received_b) == 1


# ===========================================================================
# Subscription matching
# ===========================================================================

class TestSubscriptionMatching:
    def test_emit_matches_subscription(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        wh = mgr.create_webhook(name="test", url="https://example.com/hook", owner_id=user)
        mgr.create_subscription(
            name="doc creates",
            owner_id=user,
            event_types=["object_created"],
            target_types=["document"],
            webhook_endpoint_id=wh.id,
        )

        bus.emit(
            EventType.OBJECT_CREATED,
            actor_id=user, target_type="document", target_id="d1",
        )

        # Should have created a delivery record
        deliveries = sqlite_backend.fetch_all(
            "SELECT * FROM webhook_deliveries WHERE webhook_endpoint_id = ?",
            (wh.id,),
        )
        assert len(deliveries) == 1
        assert deliveries[0]["status"] == "pending"

    def test_no_match_no_delivery(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        wh = mgr.create_webhook(name="test", url="https://example.com/hook", owner_id=user)
        mgr.create_subscription(
            name="deletes only",
            owner_id=user,
            event_types=["object_deleted"],
            webhook_endpoint_id=wh.id,
        )

        bus.emit(
            EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d1",
        )

        deliveries = sqlite_backend.fetch_all("SELECT * FROM webhook_deliveries")
        assert len(deliveries) == 0

    def test_scope_filtered_subscription(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        wh = mgr.create_webhook(name="test", url="https://example.com", owner_id=user)
        mgr.create_subscription(
            name="scope-s1",
            owner_id=user,
            scope_id="scope-abc",
            webhook_endpoint_id=wh.id,
        )

        # Different scope — no match
        bus.emit(
            EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d1",
            scope_id="scope-xyz",
        )
        assert len(sqlite_backend.fetch_all("SELECT * FROM webhook_deliveries")) == 0

        # Matching scope
        bus.emit(
            EventType.OBJECT_CREATED,
            actor_id=user, target_type="doc", target_id="d2",
            scope_id="scope-abc",
        )
        assert len(sqlite_backend.fetch_all("SELECT * FROM webhook_deliveries")) == 1


# ===========================================================================
# Query
# ===========================================================================

class TestEventQuery:
    def test_list_events(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)

        bus.emit(EventType.OBJECT_CREATED, actor_id=user, target_type="doc", target_id="d1")
        bus.emit(EventType.OBJECT_UPDATED, actor_id=user, target_type="doc", target_id="d1")
        bus.emit(EventType.OBJECT_DELETED, actor_id=user, target_type="doc", target_id="d1")

        all_events = bus.list_events()
        assert len(all_events) == 3

    def test_list_events_by_type(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)

        bus.emit(EventType.OBJECT_CREATED, actor_id=user, target_type="doc", target_id="d1")
        bus.emit(EventType.OBJECT_CREATED, actor_id=user, target_type="doc", target_id="d2")
        bus.emit(EventType.OBJECT_DELETED, actor_id=user, target_type="doc", target_id="d1")

        creates = bus.list_events(event_type=EventType.OBJECT_CREATED)
        assert len(creates) == 2

    def test_list_events_by_scope(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)

        bus.emit(EventType.OBJECT_CREATED, actor_id=user, target_type="doc", target_id="d1", scope_id="s1")
        bus.emit(EventType.OBJECT_CREATED, actor_id=user, target_type="doc", target_id="d2", scope_id="s2")

        scoped = bus.list_events(scope_id="s1")
        assert len(scoped) == 1

    def test_list_events_by_target_type(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)

        bus.emit(EventType.OBJECT_CREATED, actor_id=user, target_type="document", target_id="d1")
        bus.emit(EventType.OBJECT_CREATED, actor_id=user, target_type="note", target_id="n1")

        docs = bus.list_events(target_type="document")
        assert len(docs) == 1

    def test_count_events(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)

        bus.emit(EventType.OBJECT_CREATED, actor_id=user, target_type="doc", target_id="d1")
        bus.emit(EventType.OBJECT_CREATED, actor_id=user, target_type="doc", target_id="d2")

        assert bus.count_events() == 2
        assert bus.count_events(event_type=EventType.OBJECT_CREATED) == 2
        assert bus.count_events(event_type=EventType.OBJECT_DELETED) == 0

    def test_get_nonexistent(self, sqlite_backend):
        bus = EventBus(sqlite_backend)
        assert bus.get_event("nonexistent") is None
