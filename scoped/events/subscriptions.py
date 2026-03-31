"""Subscription management — CRUD for event subscriptions and webhook endpoints."""

from __future__ import annotations

import json
from typing import Any

from scoped.events.models import (
    EventSubscription,
    WebhookEndpoint,
    subscription_from_row,
    webhook_from_row,
)
from scoped.exceptions import AccessDeniedError
from scoped.registry.base import get_registry
from scoped.registry.kinds import RegistryKind
from scoped.registry.sqlite_store import SQLiteRegistryStore
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc


class SubscriptionManager:
    """Manage event subscriptions and webhook endpoints.

    Enforces owner-only access for mutations.
    """

    def __init__(self, backend: StorageBackend, *, audit_writer: Any | None = None) -> None:
        self._backend = backend
        self._audit = audit_writer

    # ------------------------------------------------------------------
    # Webhook endpoints
    # ------------------------------------------------------------------

    def create_webhook(
        self,
        *,
        name: str,
        url: str,
        owner_id: str,
        config: dict[str, Any] | None = None,
        scope_id: str | None = None,
    ) -> WebhookEndpoint:
        """Register a new webhook endpoint."""
        wh = WebhookEndpoint(
            id=generate_id(),
            name=name,
            owner_id=owner_id,
            url=url,
            config=config or {},
            scope_id=scope_id,
            created_at=now_utc(),
        )
        self._backend.execute(
            "INSERT INTO webhook_endpoints "
            "(id, name, owner_id, url, config_json, scope_id, created_at, lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                wh.id, wh.name, wh.owner_id, wh.url,
                json.dumps(wh.config), wh.scope_id,
                wh.created_at.isoformat(), wh.lifecycle.name,
            ),
        )

        # Auto-register (Invariant #1)
        try:
            reg = get_registry()
            entry = reg.register(
                kind=RegistryKind.WEBHOOK_ENDPOINT,
                namespace="events",
                name=f"webhook:{wh.id}",
                registered_by=owner_id,
            )
            SQLiteRegistryStore(self._backend).save_entry(entry)
        except Exception:
            pass

        if self._audit is not None:
            try:
                self._audit.record(
                    actor_id=owner_id,
                    action=ActionType.WEBHOOK_CREATE,
                    target_type="webhook_endpoint",
                    target_id=wh.id,
                    after_state={"name": name, "url": url},
                )
            except Exception:
                pass

        return wh

    def get_webhook(self, webhook_id: str) -> WebhookEndpoint | None:
        """Fetch a webhook endpoint by ID."""
        row = self._backend.fetch_one(
            "SELECT * FROM webhook_endpoints WHERE id = ?", (webhook_id,),
        )
        return webhook_from_row(row) if row else None

    def list_webhooks(
        self,
        *,
        owner_id: str | None = None,
        scope_id: str | None = None,
    ) -> list[WebhookEndpoint]:
        """List webhook endpoints with optional filters."""
        clauses: list[str] = ["lifecycle = 'ACTIVE'"]
        params: list[Any] = []

        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        if scope_id is not None:
            clauses.append("scope_id = ?")
            params.append(scope_id)

        where = " AND ".join(clauses)
        rows = self._backend.fetch_all(
            f"SELECT * FROM webhook_endpoints WHERE {where} ORDER BY created_at DESC",
            tuple(params),
        )
        return [webhook_from_row(r) for r in rows]

    def delete_webhook(self, webhook_id: str, *, principal_id: str) -> None:
        """Archive a webhook endpoint (owner only)."""
        wh = self.get_webhook(webhook_id)
        if wh is None:
            raise ValueError(f"Webhook endpoint {webhook_id} not found")
        if wh.owner_id != principal_id:
            raise AccessDeniedError(
                "Only the webhook owner can delete it",
                context={"webhook_id": webhook_id, "principal_id": principal_id},
            )
        self._backend.execute(
            "UPDATE webhook_endpoints SET lifecycle = 'ARCHIVED' WHERE id = ?",
            (webhook_id,),
        )

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def create_subscription(
        self,
        *,
        name: str,
        owner_id: str,
        event_types: list[str] | None = None,
        target_types: list[str] | None = None,
        scope_id: str | None = None,
        webhook_endpoint_id: str | None = None,
    ) -> EventSubscription:
        """Create a new event subscription."""
        sub = EventSubscription(
            id=generate_id(),
            name=name,
            owner_id=owner_id,
            event_types=event_types or [],
            target_types=target_types or [],
            scope_id=scope_id,
            webhook_endpoint_id=webhook_endpoint_id,
            created_at=now_utc(),
        )
        self._backend.execute(
            "INSERT INTO event_subscriptions "
            "(id, name, owner_id, event_types_json, target_types_json, "
            "scope_id, webhook_endpoint_id, created_at, lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sub.id, sub.name, sub.owner_id,
                json.dumps(sub.event_types),
                json.dumps(sub.target_types),
                sub.scope_id, sub.webhook_endpoint_id,
                sub.created_at.isoformat(), sub.lifecycle.name,
            ),
        )

        # Auto-register (Invariant #1)
        try:
            reg = get_registry()
            entry = reg.register(
                kind=RegistryKind.EVENT_SUBSCRIPTION,
                namespace="events",
                name=f"subscription:{sub.id}",
                registered_by=owner_id,
            )
            SQLiteRegistryStore(self._backend).save_entry(entry)
        except Exception:
            pass

        if self._audit is not None:
            try:
                self._audit.record(
                    actor_id=owner_id,
                    action=ActionType.EVENT_SUBSCRIBE,
                    target_type="event_subscription",
                    target_id=sub.id,
                    after_state={"name": name},
                )
            except Exception:
                pass

        return sub

    def get_subscription(self, subscription_id: str) -> EventSubscription | None:
        """Fetch a subscription by ID."""
        row = self._backend.fetch_one(
            "SELECT * FROM event_subscriptions WHERE id = ?", (subscription_id,),
        )
        return subscription_from_row(row) if row else None

    def list_subscriptions(
        self,
        *,
        owner_id: str | None = None,
        scope_id: str | None = None,
        active_only: bool = True,
    ) -> list[EventSubscription]:
        """List subscriptions with optional filters."""
        clauses: list[str] = []
        params: list[Any] = []

        if active_only:
            clauses.append("lifecycle = 'ACTIVE'")
        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        if scope_id is not None:
            clauses.append("scope_id = ?")
            params.append(scope_id)

        where = " AND ".join(clauses) if clauses else "1=1"
        rows = self._backend.fetch_all(
            f"SELECT * FROM event_subscriptions WHERE {where} ORDER BY created_at DESC",
            tuple(params),
        )
        return [subscription_from_row(r) for r in rows]

    def delete_subscription(self, subscription_id: str, *, principal_id: str) -> None:
        """Archive a subscription (owner only)."""
        sub = self.get_subscription(subscription_id)
        if sub is None:
            raise ValueError(f"Subscription {subscription_id} not found")
        if sub.owner_id != principal_id:
            raise AccessDeniedError(
                "Only the subscription owner can delete it",
                context={"subscription_id": subscription_id, "principal_id": principal_id},
            )
        self._backend.execute(
            "UPDATE event_subscriptions SET lifecycle = 'ARCHIVED' WHERE id = ?",
            (subscription_id,),
        )
