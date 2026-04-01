"""Tests for WebhookDelivery — delivery execution, retries, tracking."""

from __future__ import annotations

import pytest

from scoped.events.bus import EventBus
from scoped.events.models import DeliveryStatus, EventType
from scoped.events.subscriptions import SubscriptionManager
from scoped.events.webhooks import WebhookDelivery
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


def _emit_with_subscription(sqlite_backend):
    """Set up a webhook + subscription + emit an event. Returns (user, bus, mgr, delivery_mgr)."""
    user = _setup_principal(sqlite_backend)
    bus = EventBus(sqlite_backend)
    mgr = SubscriptionManager(sqlite_backend)

    wh = mgr.create_webhook(name="hook", url="https://example.com/hook", owner_id=user)
    mgr.create_subscription(
        name="all", owner_id=user, webhook_endpoint_id=wh.id,
    )

    bus.emit(EventType.OBJECT_CREATED, actor_id=user, target_type="doc", target_id="d1")

    delivery = WebhookDelivery(sqlite_backend)
    return user, bus, mgr, delivery


# ===========================================================================
# Deliver pending
# ===========================================================================

class TestDeliverPending:
    def test_deliver_pending_default_transport(self, sqlite_backend):
        _, _, _, delivery = _emit_with_subscription(sqlite_backend)

        attempts = delivery.deliver_pending()

        assert len(attempts) == 1
        assert attempts[0].status == DeliveryStatus.DELIVERED
        assert attempts[0].response_status == 200

    def test_deliver_pending_updates_status(self, sqlite_backend):
        _, _, _, delivery = _emit_with_subscription(sqlite_backend)

        delivery.deliver_pending()

        rows = sqlite_backend.fetch_all("SELECT * FROM webhook_deliveries")
        assert len(rows) == 1
        assert rows[0]["status"] == "delivered"

    def test_deliver_pending_custom_transport(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        wh = mgr.create_webhook(name="hook", url="https://example.com", owner_id=user)
        mgr.create_subscription(name="all", owner_id=user, webhook_endpoint_id=wh.id)
        bus.emit(EventType.OBJECT_CREATED, actor_id=user, target_type="doc", target_id="d1")

        call_log = []

        def my_transport(endpoint, event):
            call_log.append((endpoint.url, event.event_type))
            return (201, "created")

        delivery = WebhookDelivery(sqlite_backend, transport=my_transport)
        attempts = delivery.deliver_pending()

        assert len(call_log) == 1
        assert call_log[0][0] == "https://example.com"
        assert attempts[0].response_status == 201

    def test_deliver_nothing_pending(self, sqlite_backend):
        delivery = WebhookDelivery(sqlite_backend)
        attempts = delivery.deliver_pending()
        assert len(attempts) == 0


# ===========================================================================
# Failed deliveries
# ===========================================================================

class TestFailedDeliveries:
    def test_transport_failure(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        wh = mgr.create_webhook(name="hook", url="https://example.com", owner_id=user)
        mgr.create_subscription(name="all", owner_id=user, webhook_endpoint_id=wh.id)
        bus.emit(EventType.OBJECT_CREATED, actor_id=user, target_type="doc", target_id="d1")

        def failing_transport(endpoint, event):
            return (500, "Internal Server Error")

        delivery = WebhookDelivery(sqlite_backend, transport=failing_transport)
        attempts = delivery.deliver_pending()

        assert len(attempts) == 1
        assert attempts[0].status == DeliveryStatus.FAILED
        assert attempts[0].response_status == 500

    def test_transport_exception(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        wh = mgr.create_webhook(name="hook", url="https://example.com", owner_id=user)
        mgr.create_subscription(name="all", owner_id=user, webhook_endpoint_id=wh.id)
        bus.emit(EventType.OBJECT_CREATED, actor_id=user, target_type="doc", target_id="d1")

        def exploding_transport(endpoint, event):
            raise ConnectionError("Connection refused")

        delivery = WebhookDelivery(sqlite_backend, transport=exploding_transport)
        attempts = delivery.deliver_pending()

        assert len(attempts) == 1
        assert attempts[0].status == DeliveryStatus.FAILED
        assert "Connection refused" in attempts[0].error_message


# ===========================================================================
# Retry
# ===========================================================================

class TestRetry:
    def test_retry_failed(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        wh = mgr.create_webhook(name="hook", url="https://example.com", owner_id=user)
        mgr.create_subscription(name="all", owner_id=user, webhook_endpoint_id=wh.id)
        bus.emit(EventType.OBJECT_CREATED, actor_id=user, target_type="doc", target_id="d1")

        # First attempt fails
        failing = WebhookDelivery(sqlite_backend, transport=lambda e, ev: (500, "fail"))
        failing.deliver_pending()

        # Retry with working transport (backoff_base=0 to retry immediately in tests)
        working = WebhookDelivery(sqlite_backend, transport=lambda e, ev: (200, "ok"))
        retries = working.retry_failed(backoff_base=0)

        assert len(retries) == 1
        assert retries[0].status == DeliveryStatus.DELIVERED

    def test_no_retry_after_max(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        wh = mgr.create_webhook(name="hook", url="https://example.com", owner_id=user)
        mgr.create_subscription(name="all", owner_id=user, webhook_endpoint_id=wh.id)
        bus.emit(EventType.OBJECT_CREATED, actor_id=user, target_type="doc", target_id="d1")

        # Exhaust retries (max_retries=2)
        delivery = WebhookDelivery(
            sqlite_backend,
            transport=lambda e, ev: (500, "fail"),
            max_retries=2,
        )
        delivery.deliver_pending()   # attempt 1
        delivery.retry_failed(backoff_base=0)      # attempt 2

        # No more retries — already at max
        retries = delivery.retry_failed(backoff_base=0)
        assert len(retries) == 0


# ===========================================================================
# Query
# ===========================================================================

class TestDeliveryQuery:
    def test_get_deliveries_by_event(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        bus = EventBus(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        wh = mgr.create_webhook(name="hook", url="https://example.com", owner_id=user)
        mgr.create_subscription(name="all", owner_id=user, webhook_endpoint_id=wh.id)

        event = bus.emit(EventType.OBJECT_CREATED, actor_id=user, target_type="doc", target_id="d1")

        delivery = WebhookDelivery(sqlite_backend)
        rows = delivery.get_deliveries(event_id=event.id)
        assert len(rows) == 1

    def test_get_deliveries_by_status(self, sqlite_backend):
        _, _, _, delivery = _emit_with_subscription(sqlite_backend)

        pending = delivery.get_deliveries(status=DeliveryStatus.PENDING)
        assert len(pending) == 1

        delivery.deliver_pending()

        delivered = delivery.get_deliveries(status=DeliveryStatus.DELIVERED)
        assert len(delivered) == 1
        pending = delivery.get_deliveries(status=DeliveryStatus.PENDING)
        assert len(pending) == 0
