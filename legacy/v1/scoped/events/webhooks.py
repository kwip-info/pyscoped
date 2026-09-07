"""Webhook delivery — outbound HTTP delivery with retry tracking.

In a real deployment, delivery would call external URLs via HTTP.
The framework layer handles persistence, retry bookkeeping, and
status tracking.  Actual HTTP transport is pluggable via a
delivery function.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import sqlalchemy as sa

from scoped.events.models import (
    DeliveryAttempt,
    DeliveryStatus,
    Event,
    WebhookEndpoint,
    event_from_row,
    webhook_from_row,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import events, webhook_deliveries, webhook_endpoints
from scoped.storage.interface import StorageBackend
from scoped.types import generate_id, now_utc
from scoped._stability import experimental


# Type alias for pluggable delivery transport
DeliveryTransport = Callable[[WebhookEndpoint, Event], tuple[int, str]]
"""(endpoint, event) -> (status_code, response_body)"""


@experimental()
class WebhookDelivery:
    """Manage webhook delivery lifecycle: queue, attempt, retry, track.

    Parameters
    ----------
    backend:
        Storage backend for persistence.
    transport:
        Optional pluggable function that performs the actual HTTP call.
        Signature: ``(endpoint, event) -> (status_code, response_body)``.
        If not provided, delivery simulation returns ``(200, "ok")``.
    max_retries:
        Maximum number of retry attempts before marking as failed.
    """

    def __init__(
        self,
        backend: StorageBackend,
        *,
        transport: DeliveryTransport | None = None,
        max_retries: int = 3,
        webhook_key: bytes | None = None,
    ) -> None:
        self._backend = backend
        self._transport = transport or self._default_transport
        self._max_retries = max_retries
        self._webhook_key = webhook_key

    # ------------------------------------------------------------------
    # Delivery execution
    # ------------------------------------------------------------------

    def deliver_pending(self) -> list[DeliveryAttempt]:
        """Attempt delivery for all pending webhook deliveries.

        Returns a list of :class:`DeliveryAttempt` records for each
        delivery attempted.
        """
        stmt = (
            sa.select(webhook_deliveries)
            .where(webhook_deliveries.c.status == "pending")
            .order_by(webhook_deliveries.c.attempted_at.asc())
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        attempts: list[DeliveryAttempt] = []
        for row in rows:
            attempt = self._attempt_delivery(row)
            attempts.append(attempt)
        return attempts

    def get_deliveries(
        self,
        *,
        event_id: str | None = None,
        webhook_endpoint_id: str | None = None,
        status: DeliveryStatus | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query delivery records with optional filters."""
        stmt = sa.select(webhook_deliveries)

        if event_id is not None:
            stmt = stmt.where(webhook_deliveries.c.event_id == event_id)
        if webhook_endpoint_id is not None:
            stmt = stmt.where(webhook_deliveries.c.webhook_endpoint_id == webhook_endpoint_id)
        if status is not None:
            stmt = stmt.where(webhook_deliveries.c.status == status.value)

        stmt = stmt.order_by(webhook_deliveries.c.attempted_at.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        return self._backend.fetch_all(sql, params)

    def retry_failed(self, *, backoff_base: int = 60) -> list[DeliveryAttempt]:
        """Retry failed deliveries that haven't exceeded max retries.

        Uses exponential backoff: only retries deliveries where enough
        time has elapsed since the last attempt. The delay is
        ``backoff_base * 2^(attempt_number - 1)`` seconds.

        Args:
            backoff_base: Base delay in seconds (default 60). First retry
                          waits 60s, second 120s, third 240s, etc.
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        stmt = (
            sa.select(webhook_deliveries)
            .where(
                webhook_deliveries.c.status == "failed",
                webhook_deliveries.c.attempt_number < self._max_retries,
            )
            .order_by(webhook_deliveries.c.attempted_at.asc())
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        attempts: list[DeliveryAttempt] = []
        for row in rows:
            # Exponential backoff check
            attempt_num = row.get("attempt_number", 0)
            delay_seconds = backoff_base * (2 ** max(0, attempt_num - 1))
            attempted_at = row.get("attempted_at")
            if attempted_at:
                last_attempt = datetime.fromisoformat(attempted_at)
                if last_attempt.tzinfo is None:
                    last_attempt = last_attempt.replace(tzinfo=timezone.utc)
                elapsed = (now - last_attempt).total_seconds()
                if elapsed < delay_seconds:
                    continue  # Not enough time has passed

            update_stmt = (
                sa.update(webhook_deliveries)
                .where(webhook_deliveries.c.id == row["id"])
                .values(status="retrying")
            )
            sql_u, params_u = compile_for(update_stmt, self._backend.dialect)
            self._backend.execute(sql_u, params_u)
            row["status"] = "retrying"
            attempt = self._attempt_delivery(row)
            attempts.append(attempt)
        return attempts

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _attempt_delivery(self, delivery_row: dict[str, Any]) -> DeliveryAttempt:
        """Attempt to deliver a single webhook."""
        delivery_id = delivery_row["id"]
        event_id = delivery_row["event_id"]
        endpoint_id = delivery_row["webhook_endpoint_id"]
        attempt_number = delivery_row.get("attempt_number", 0) + 1

        # Fetch the event
        evt_stmt = sa.select(events).where(events.c.id == event_id)
        sql_e, params_e = compile_for(evt_stmt, self._backend.dialect)
        event_row = self._backend.fetch_one(sql_e, params_e)

        # Fetch the endpoint
        ep_stmt = sa.select(webhook_endpoints).where(webhook_endpoints.c.id == endpoint_id)
        sql_ep, params_ep = compile_for(ep_stmt, self._backend.dialect)
        endpoint_row = self._backend.fetch_one(sql_ep, params_ep)

        if event_row is None or endpoint_row is None:
            # Mark as failed — missing data
            attempt = DeliveryAttempt(
                id=generate_id(),
                event_id=event_id,
                webhook_endpoint_id=endpoint_id,
                subscription_id=delivery_row.get("subscription_id", ""),
                status=DeliveryStatus.FAILED,
                attempted_at=now_utc(),
                error_message="Event or endpoint not found",
                attempt_number=attempt_number,
            )
            fail_stmt = (
                sa.update(webhook_deliveries)
                .where(webhook_deliveries.c.id == delivery_id)
                .values(
                    status="failed",
                    attempt_number=attempt_number,
                    response_body="Event or endpoint not found",
                )
            )
            sql_f, params_f = compile_for(fail_stmt, self._backend.dialect)
            self._backend.execute(sql_f, params_f)
            return attempt

        event = event_from_row(event_row)
        endpoint = webhook_from_row(endpoint_row)

        # Decrypt sensitive config fields if a webhook key is configured
        if self._webhook_key is not None:
            from scoped.events.crypto import decrypt_config
            decrypted = decrypt_config(endpoint.config, self._webhook_key)
            endpoint = WebhookEndpoint(
                id=endpoint.id,
                name=endpoint.name,
                owner_id=endpoint.owner_id,
                url=endpoint.url,
                config=decrypted,
                scope_id=endpoint.scope_id,
                created_at=endpoint.created_at,
                lifecycle=endpoint.lifecycle,
            )

        try:
            status_code, response_body = self._transport(endpoint, event)
            success = 200 <= status_code < 300
            status = DeliveryStatus.DELIVERED if success else DeliveryStatus.FAILED

            attempt = DeliveryAttempt(
                id=generate_id(),
                event_id=event_id,
                webhook_endpoint_id=endpoint_id,
                subscription_id=delivery_row.get("subscription_id", ""),
                status=status,
                attempted_at=now_utc(),
                response_status=status_code,
                response_body=response_body,
                attempt_number=attempt_number,
            )

            ok_stmt = (
                sa.update(webhook_deliveries)
                .where(webhook_deliveries.c.id == delivery_id)
                .values(
                    status=status.value,
                    attempt_number=attempt_number,
                    response_status=status_code,
                    response_body=response_body,
                )
            )
            sql_ok, params_ok = compile_for(ok_stmt, self._backend.dialect)
            self._backend.execute(sql_ok, params_ok)
        except Exception as exc:
            attempt = DeliveryAttempt(
                id=generate_id(),
                event_id=event_id,
                webhook_endpoint_id=endpoint_id,
                subscription_id=delivery_row.get("subscription_id", ""),
                status=DeliveryStatus.FAILED,
                attempted_at=now_utc(),
                error_message=str(exc),
                attempt_number=attempt_number,
            )
            err_stmt = (
                sa.update(webhook_deliveries)
                .where(webhook_deliveries.c.id == delivery_id)
                .values(
                    status="failed",
                    attempt_number=attempt_number,
                    error_message=str(exc),
                )
            )
            sql_err, params_err = compile_for(err_stmt, self._backend.dialect)
            self._backend.execute(sql_err, params_err)

        return attempt

    @staticmethod
    def _default_transport(endpoint: WebhookEndpoint, event: Event) -> tuple[int, str]:
        """Default no-op transport for testing — always succeeds."""
        return (200, "ok")

    @staticmethod
    def http_transport(
        endpoint: WebhookEndpoint,
        event: Event,
        *,
        timeout: int = 10,
    ) -> tuple[int, str]:
        """Real HTTP transport using stdlib urllib.

        Posts the event as JSON to the endpoint URL. Returns
        ``(status_code, response_body)``.

        Pass as ``transport=WebhookDelivery.http_transport`` when
        constructing the delivery manager for production use.
        """
        import urllib.request
        import urllib.error

        payload = json.dumps({
            "event_id": event.id,
            "event_type": event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type),
            "actor_id": event.actor_id,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "timestamp": event.timestamp.isoformat(),
            "scope_id": event.scope_id,
            "data": event.data,
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "pyscoped-webhook/1.0",
        }
        # Merge endpoint config headers if present
        extra_headers = endpoint.config.get("headers", {})
        headers.update(extra_headers)

        req = urllib.request.Request(
            endpoint.url,
            data=payload,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return (resp.status, body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            return (exc.code, body)
        except urllib.error.URLError as exc:
            raise ConnectionError(f"Webhook delivery failed: {exc.reason}") from exc
