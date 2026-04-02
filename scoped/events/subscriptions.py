"""Subscription management — CRUD for event subscriptions and webhook endpoints."""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

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
from scoped.storage._query import compile_for
from scoped.storage._schema import event_subscriptions, webhook_endpoints
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc
from scoped._stability import experimental


@experimental()
class SubscriptionManager:
    """Manage event subscriptions and webhook endpoints.

    Enforces owner-only access for mutations.
    """

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
        webhook_key: bytes | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer
        self._webhook_key = webhook_key

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
        raw_config = config or {}

        # Encrypt sensitive fields before persisting
        if self._webhook_key is not None:
            from scoped.events.crypto import encrypt_config
            store_config = encrypt_config(raw_config, self._webhook_key)
        else:
            store_config = raw_config

        wh = WebhookEndpoint(
            id=generate_id(),
            name=name,
            owner_id=owner_id,
            url=url,
            config=raw_config,
            scope_id=scope_id,
            created_at=now_utc(),
        )
        stmt = sa.insert(webhook_endpoints).values(
            id=wh.id,
            name=wh.name,
            owner_id=wh.owner_id,
            url=wh.url,
            config_json=json.dumps(store_config),
            scope_id=wh.scope_id,
            created_at=wh.created_at.isoformat(),
            lifecycle=wh.lifecycle.name,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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
        stmt = sa.select(webhook_endpoints).where(webhook_endpoints.c.id == webhook_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            return None
        wh = webhook_from_row(row)
        if self._webhook_key is not None:
            from scoped.events.crypto import decrypt_config
            decrypted = decrypt_config(wh.config, self._webhook_key)
            wh = WebhookEndpoint(
                id=wh.id,
                name=wh.name,
                owner_id=wh.owner_id,
                url=wh.url,
                config=decrypted,
                scope_id=wh.scope_id,
                created_at=wh.created_at,
                lifecycle=wh.lifecycle,
            )
        return wh

    def list_webhooks(
        self,
        *,
        owner_id: str | None = None,
        scope_id: str | None = None,
    ) -> list[WebhookEndpoint]:
        """List webhook endpoints with optional filters."""
        stmt = sa.select(webhook_endpoints).where(
            webhook_endpoints.c.lifecycle == "ACTIVE"
        )

        if owner_id is not None:
            stmt = stmt.where(webhook_endpoints.c.owner_id == owner_id)
        if scope_id is not None:
            stmt = stmt.where(webhook_endpoints.c.scope_id == scope_id)

        stmt = stmt.order_by(webhook_endpoints.c.created_at.desc())
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        results = [webhook_from_row(r) for r in rows]
        if self._webhook_key is not None:
            from scoped.events.crypto import decrypt_config
            decrypted_results = []
            for wh in results:
                decrypted = decrypt_config(wh.config, self._webhook_key)
                decrypted_results.append(
                    WebhookEndpoint(
                        id=wh.id,
                        name=wh.name,
                        owner_id=wh.owner_id,
                        url=wh.url,
                        config=decrypted,
                        scope_id=wh.scope_id,
                        created_at=wh.created_at,
                        lifecycle=wh.lifecycle,
                    )
                )
            return decrypted_results
        return results

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
        stmt = (
            sa.update(webhook_endpoints)
            .where(webhook_endpoints.c.id == webhook_id)
            .values(lifecycle="ARCHIVED")
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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
        stmt = sa.insert(event_subscriptions).values(
            id=sub.id,
            name=sub.name,
            owner_id=sub.owner_id,
            event_types_json=json.dumps(sub.event_types),
            target_types_json=json.dumps(sub.target_types),
            scope_id=sub.scope_id,
            webhook_endpoint_id=sub.webhook_endpoint_id,
            created_at=sub.created_at.isoformat(),
            lifecycle=sub.lifecycle.name,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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
        stmt = sa.select(event_subscriptions).where(
            event_subscriptions.c.id == subscription_id
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return subscription_from_row(row) if row else None

    def list_subscriptions(
        self,
        *,
        owner_id: str | None = None,
        scope_id: str | None = None,
        active_only: bool = True,
    ) -> list[EventSubscription]:
        """List subscriptions with optional filters."""
        stmt = sa.select(event_subscriptions)

        if active_only:
            stmt = stmt.where(event_subscriptions.c.lifecycle == "ACTIVE")
        if owner_id is not None:
            stmt = stmt.where(event_subscriptions.c.owner_id == owner_id)
        if scope_id is not None:
            stmt = stmt.where(event_subscriptions.c.scope_id == scope_id)

        stmt = stmt.order_by(event_subscriptions.c.created_at.desc())
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
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
        stmt = (
            sa.update(event_subscriptions)
            .where(event_subscriptions.c.id == subscription_id)
            .values(lifecycle="ARCHIVED")
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
