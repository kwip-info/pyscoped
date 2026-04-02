"""Event bus — dispatch events to matching subscriptions.

The bus is the central dispatcher.  Framework actions call ``emit()``
to produce events.  The bus persists the event, finds matching
subscriptions, and queues webhook deliveries.

In-process listeners can also be registered via ``on()`` for
synchronous callbacks (useful for testing and single-process setups).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import sqlalchemy as sa

from scoped.events.models import (
    Event,
    EventType,
    event_from_row,
    subscription_from_row,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import events, event_subscriptions, webhook_deliveries
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc
from scoped._stability import experimental


# Type alias for in-process event listeners
EventListener = Callable[[Event], None]

_WILDCARD = "*"


@experimental()
class EventBus:
    """Central event dispatcher.

    The bus:
    1. Persists events to the ``events`` table.
    2. Matches events against active subscriptions.
    3. Queues webhook deliveries for matching subscriptions that have
       a ``webhook_endpoint_id``.
    4. Calls in-process listeners registered via :meth:`on`.

    Parameters
    ----------
    backend:
        Storage backend for persistence.
    """

    def __init__(self, backend: StorageBackend, *, audit_writer: Any | None = None) -> None:
        self._backend = backend
        self._audit = audit_writer
        self._listeners: dict[str, list[EventListener]] = {}  # event_type -> listeners

    # ------------------------------------------------------------------
    # In-process listeners
    # ------------------------------------------------------------------

    def on(self, event_type: EventType | str, listener: EventListener) -> None:
        """Register an in-process listener for a specific event type."""
        key = event_type.value if isinstance(event_type, EventType) else event_type
        self._listeners.setdefault(key, []).append(listener)

    def off(self, event_type: EventType | str, listener: EventListener) -> None:
        """Remove an in-process listener."""
        key = event_type.value if isinstance(event_type, EventType) else event_type
        listeners = self._listeners.get(key, [])
        if listener in listeners:
            listeners.remove(listener)

    def on_any(self, listener: EventListener) -> None:
        """Register an in-process listener that receives ALL event types."""
        self._listeners.setdefault(_WILDCARD, []).append(listener)

    def off_any(self, listener: EventListener) -> None:
        """Remove a wildcard in-process listener."""
        listeners = self._listeners.get(_WILDCARD, [])
        if listener in listeners:
            listeners.remove(listener)

    # ------------------------------------------------------------------
    # Emit
    # ------------------------------------------------------------------

    def emit(
        self,
        event_type: EventType,
        *,
        actor_id: str,
        target_type: str,
        target_id: str,
        scope_id: str | None = None,
        data: dict[str, Any] | None = None,
        source_trace_id: str | None = None,
    ) -> Event:
        """Emit an event: persist, match subscriptions, notify listeners.

        Returns the created :class:`Event`.
        """
        event = Event(
            id=generate_id(),
            event_type=event_type,
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            timestamp=now_utc(),
            scope_id=scope_id,
            data=data or {},
            source_trace_id=source_trace_id,
        )

        # Persist
        self._persist_event(event)

        # Match subscriptions and queue deliveries
        matched = self._match_subscriptions(event)
        for sub in matched:
            if sub.webhook_endpoint_id:
                self._queue_delivery(event, sub)

        # In-process listeners (type-specific)
        for listener in self._listeners.get(event.event_type.value, []):
            listener(event)

        # In-process listeners (wildcard — receive all event types)
        for listener in self._listeners.get(_WILDCARD, []):
            listener(event)

        # Audit trail
        if self._audit is not None:
            try:
                self._audit.record(
                    actor_id=actor_id,
                    action=ActionType.EVENT_EMIT,
                    target_type="event",
                    target_id=event.id,
                    after_state={
                        "event_type": event_type.value,
                        "target_type": target_type,
                        "target_id": target_id,
                    },
                )
            except Exception:
                pass

        return event

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_event(self, event_id: str) -> Event | None:
        """Fetch a single event by ID."""
        stmt = sa.select(events).where(events.c.id == event_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return event_from_row(row) if row else None

    def list_events(
        self,
        *,
        event_type: EventType | None = None,
        actor_id: str | None = None,
        scope_id: str | None = None,
        target_type: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """List events with optional filters."""
        stmt = sa.select(events)

        if event_type is not None:
            stmt = stmt.where(events.c.event_type == event_type.value)
        if actor_id is not None:
            stmt = stmt.where(events.c.actor_id == actor_id)
        if scope_id is not None:
            stmt = stmt.where(events.c.scope_id == scope_id)
        if target_type is not None:
            stmt = stmt.where(events.c.target_type == target_type)

        stmt = stmt.order_by(events.c.timestamp.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [event_from_row(r) for r in rows]

    def count_events(
        self,
        *,
        event_type: EventType | None = None,
        scope_id: str | None = None,
    ) -> int:
        """Count events with optional filters."""
        stmt = sa.select(sa.func.count().label("cnt")).select_from(events)

        if event_type is not None:
            stmt = stmt.where(events.c.event_type == event_type.value)
        if scope_id is not None:
            stmt = stmt.where(events.c.scope_id == scope_id)

        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _persist_event(self, event: Event) -> None:
        stmt = sa.insert(events).values(
            id=event.id,
            event_type=event.event_type.value,
            actor_id=event.actor_id,
            target_type=event.target_type,
            target_id=event.target_id,
            timestamp=event.timestamp.isoformat(),
            scope_id=event.scope_id,
            data_json=json.dumps(event.data),
            source_trace_id=event.source_trace_id,
            lifecycle=event.lifecycle.name,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

    def _match_subscriptions(self, event: Event) -> list:
        """Find active subscriptions that match this event."""
        stmt = sa.select(event_subscriptions).where(
            event_subscriptions.c.lifecycle == "ACTIVE"
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        subs = [subscription_from_row(r) for r in rows]
        return [s for s in subs if s.matches(event)]

    def _queue_delivery(self, event: Event, subscription) -> None:
        """Create a pending delivery record for a matched subscription."""
        stmt = sa.insert(webhook_deliveries).values(
            id=generate_id(),
            event_id=event.id,
            webhook_endpoint_id=subscription.webhook_endpoint_id,
            subscription_id=subscription.id,
            status="pending",
            attempted_at=now_utc().isoformat(),
            attempt_number=0,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
