"""Tests for SubscriptionManager — CRUD for subscriptions and webhooks."""

from __future__ import annotations

import pytest

from scoped.events.subscriptions import SubscriptionManager
from scoped.exceptions import AccessDeniedError
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
# Webhook endpoints
# ===========================================================================

class TestWebhookCreate:
    def test_create_webhook(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        wh = mgr.create_webhook(
            name="My Hook", url="https://example.com/hook", owner_id=user,
        )

        assert wh.name == "My Hook"
        assert wh.url == "https://example.com/hook"
        assert wh.owner_id == user

    def test_create_webhook_with_config(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        wh = mgr.create_webhook(
            name="Configured", url="https://example.com",
            owner_id=user, config={"timeout": 30, "auth": "bearer"},
        )

        assert wh.config == {"timeout": 30, "auth": "bearer"}

    def test_create_webhook_with_scope(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        wh = mgr.create_webhook(
            name="Scoped", url="https://example.com",
            owner_id=user, scope_id="scope1",
        )

        assert wh.scope_id == "scope1"


class TestWebhookRead:
    def test_get_webhook(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        wh = mgr.create_webhook(name="test", url="https://example.com", owner_id=user)
        fetched = mgr.get_webhook(wh.id)

        assert fetched is not None
        assert fetched.id == wh.id
        assert fetched.name == "test"

    def test_get_webhook_not_found(self, sqlite_backend):
        mgr = SubscriptionManager(sqlite_backend)
        assert mgr.get_webhook("nonexistent") is None

    def test_list_webhooks(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        mgr.create_webhook(name="a", url="https://a.com", owner_id=user)
        mgr.create_webhook(name="b", url="https://b.com", owner_id=user)

        hooks = mgr.list_webhooks(owner_id=user)
        assert len(hooks) == 2

    def test_list_webhooks_by_scope(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        mgr.create_webhook(name="a", url="https://a.com", owner_id=user, scope_id="s1")
        mgr.create_webhook(name="b", url="https://b.com", owner_id=user, scope_id="s2")

        hooks = mgr.list_webhooks(scope_id="s1")
        assert len(hooks) == 1


class TestWebhookDelete:
    def test_delete_webhook(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        wh = mgr.create_webhook(name="test", url="https://example.com", owner_id=user)
        mgr.delete_webhook(wh.id, principal_id=user)

        # Should be archived
        hooks = mgr.list_webhooks(owner_id=user)
        assert len(hooks) == 0

    def test_delete_non_owner_denied(self, sqlite_backend):
        user1 = _setup_principal(sqlite_backend)
        user2 = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        wh = mgr.create_webhook(name="test", url="https://example.com", owner_id=user1)

        with pytest.raises(AccessDeniedError):
            mgr.delete_webhook(wh.id, principal_id=user2)

    def test_delete_nonexistent_raises(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        with pytest.raises(ValueError, match="not found"):
            mgr.delete_webhook("nonexistent", principal_id=user)


# ===========================================================================
# Subscriptions
# ===========================================================================

class TestSubscriptionCreate:
    def test_create_subscription(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        sub = mgr.create_subscription(
            name="all events", owner_id=user,
        )

        assert sub.name == "all events"
        assert sub.owner_id == user
        assert sub.event_types == []
        assert sub.target_types == []

    def test_create_with_filters(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        sub = mgr.create_subscription(
            name="doc creates", owner_id=user,
            event_types=["object_created"],
            target_types=["document"],
            scope_id="scope1",
        )

        assert sub.event_types == ["object_created"]
        assert sub.target_types == ["document"]
        assert sub.scope_id == "scope1"

    def test_create_with_webhook(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        wh = mgr.create_webhook(name="hook", url="https://example.com", owner_id=user)
        sub = mgr.create_subscription(
            name="with webhook", owner_id=user, webhook_endpoint_id=wh.id,
        )

        assert sub.webhook_endpoint_id == wh.id


class TestSubscriptionRead:
    def test_get_subscription(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        sub = mgr.create_subscription(name="test", owner_id=user)
        fetched = mgr.get_subscription(sub.id)

        assert fetched is not None
        assert fetched.id == sub.id

    def test_get_not_found(self, sqlite_backend):
        mgr = SubscriptionManager(sqlite_backend)
        assert mgr.get_subscription("nonexistent") is None

    def test_list_subscriptions(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        mgr.create_subscription(name="a", owner_id=user)
        mgr.create_subscription(name="b", owner_id=user)

        subs = mgr.list_subscriptions(owner_id=user)
        assert len(subs) == 2

    def test_list_by_scope(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        mgr.create_subscription(name="a", owner_id=user, scope_id="s1")
        mgr.create_subscription(name="b", owner_id=user, scope_id="s2")

        subs = mgr.list_subscriptions(scope_id="s1")
        assert len(subs) == 1


class TestSubscriptionDelete:
    def test_delete_subscription(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        sub = mgr.create_subscription(name="test", owner_id=user)
        mgr.delete_subscription(sub.id, principal_id=user)

        subs = mgr.list_subscriptions(owner_id=user)
        assert len(subs) == 0

    def test_delete_non_owner_denied(self, sqlite_backend):
        user1 = _setup_principal(sqlite_backend)
        user2 = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        sub = mgr.create_subscription(name="test", owner_id=user1)

        with pytest.raises(AccessDeniedError):
            mgr.delete_subscription(sub.id, principal_id=user2)

    def test_delete_nonexistent_raises(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        mgr = SubscriptionManager(sqlite_backend)

        with pytest.raises(ValueError, match="not found"):
            mgr.delete_subscription("nonexistent", principal_id=user)
