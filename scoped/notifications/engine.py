"""Notification engine — evaluate rules and generate notifications from events."""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from scoped.events.models import Event
from scoped.registry.base import get_registry
from scoped.registry.kinds import RegistryKind
from scoped.registry.sqlite_store import SQLiteRegistryStore
from scoped.notifications.models import (
    Notification,
    NotificationChannel,
    NotificationRule,
    NotificationStatus,
    notification_from_row,
    rule_from_row,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import notification_rules, notifications
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc
from scoped._stability import experimental


@experimental()
class NotificationEngine:
    """Evaluate notification rules against events, generate notifications.

    The engine:
    1. Receives an event (from the EventBus or direct call).
    2. Finds all active notification rules whose filters match.
    3. For each matching rule, generates a notification per recipient.
    4. Persists the notifications.

    Parameters
    ----------
    backend:
        Storage backend for persistence.
    """

    def __init__(self, backend: StorageBackend, *, audit_writer: Any | None = None) -> None:
        self._backend = backend
        self._audit = audit_writer

    # ------------------------------------------------------------------
    # Rule management
    # ------------------------------------------------------------------

    def create_rule(
        self,
        *,
        name: str,
        owner_id: str,
        event_types: list[str] | None = None,
        target_types: list[str] | None = None,
        scope_id: str | None = None,
        recipient_ids: list[str],
        channel: NotificationChannel = NotificationChannel.IN_APP,
        title_template: str = "{event_type}",
        body_template: str = "{target_type} {target_id}",
    ) -> NotificationRule:
        """Create a notification rule."""
        rule = NotificationRule(
            id=generate_id(),
            name=name,
            owner_id=owner_id,
            event_types=event_types or [],
            target_types=target_types or [],
            scope_id=scope_id,
            recipient_ids=recipient_ids,
            channel=channel,
            title_template=title_template,
            body_template=body_template,
            created_at=now_utc(),
        )
        stmt = sa.insert(notification_rules).values(
            id=rule.id,
            name=rule.name,
            owner_id=rule.owner_id,
            event_types_json=json.dumps(rule.event_types),
            target_types_json=json.dumps(rule.target_types),
            scope_id=rule.scope_id,
            recipient_ids_json=json.dumps(rule.recipient_ids),
            channel=rule.channel.value,
            title_template=rule.title_template,
            body_template=rule.body_template,
            created_at=rule.created_at.isoformat(),
            lifecycle=rule.lifecycle.name,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        # Auto-register (Invariant #1)
        try:
            reg = get_registry()
            entry = reg.register(
                kind=RegistryKind.NOTIFICATION_RULE,
                namespace="notifications",
                name=f"notification_rule:{rule.id}",
                registered_by=owner_id,
            )
            SQLiteRegistryStore(self._backend).save_entry(entry)
        except Exception:
            pass

        if self._audit is not None:
            try:
                self._audit.record(
                    actor_id=owner_id,
                    action=ActionType.NOTIFICATION_RULE_CREATE,
                    target_type="notification_rule",
                    target_id=rule.id,
                    after_state={"name": name, "channel": channel.value},
                )
            except Exception:
                pass

        return rule

    def get_rule(self, rule_id: str) -> NotificationRule | None:
        stmt = sa.select(notification_rules).where(notification_rules.c.id == rule_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return rule_from_row(row) if row else None

    def list_rules(
        self,
        *,
        owner_id: str | None = None,
        active_only: bool = True,
    ) -> list[NotificationRule]:
        stmt = sa.select(notification_rules)
        if active_only:
            stmt = stmt.where(notification_rules.c.lifecycle == "ACTIVE")
        if owner_id is not None:
            stmt = stmt.where(notification_rules.c.owner_id == owner_id)
        stmt = stmt.order_by(notification_rules.c.created_at.desc())
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [rule_from_row(r) for r in rows]

    def archive_rule(self, rule_id: str) -> None:
        stmt = (
            sa.update(notification_rules)
            .where(notification_rules.c.id == rule_id)
            .values(lifecycle="ARCHIVED")
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit is not None:
            try:
                self._audit.record(
                    actor_id="system",
                    action=ActionType.LIFECYCLE_CHANGE,
                    target_type="notification_rule",
                    target_id=rule_id,
                    before_state={"lifecycle": "ACTIVE"},
                    after_state={"lifecycle": "ARCHIVED"},
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Process event
    # ------------------------------------------------------------------

    def process_event(self, event: Event) -> list[Notification]:
        """Evaluate all rules against an event and generate notifications.

        Returns the list of newly created notifications.
        """
        rules = self._match_rules(event)
        notifications_list: list[Notification] = []

        for rule in rules:
            for recipient_id in rule.recipient_ids:
                title = rule.title_template.format(
                    event_type=event.event_type.value,
                    target_type=event.target_type,
                    target_id=event.target_id,
                    actor_id=event.actor_id,
                )
                body = rule.body_template.format(
                    event_type=event.event_type.value,
                    target_type=event.target_type,
                    target_id=event.target_id,
                    actor_id=event.actor_id,
                )
                notification = self._create_notification(
                    recipient_id=recipient_id,
                    title=title,
                    body=body,
                    channel=rule.channel,
                    source_event_id=event.id,
                    source_rule_id=rule.id,
                    scope_id=event.scope_id,
                    data=event.data,
                )
                notifications_list.append(notification)

        return notifications_list

    # ------------------------------------------------------------------
    # Notification CRUD
    # ------------------------------------------------------------------

    def get_notification(self, notification_id: str) -> Notification | None:
        stmt = sa.select(notifications).where(notifications.c.id == notification_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return notification_from_row(row) if row else None

    def list_notifications(
        self,
        *,
        recipient_id: str,
        status: NotificationStatus | None = None,
        limit: int = 100,
    ) -> list[Notification]:
        stmt = sa.select(notifications).where(
            notifications.c.recipient_id == recipient_id,
            notifications.c.lifecycle == "ACTIVE",
        )
        if status is not None:
            stmt = stmt.where(notifications.c.status == status.value)
        stmt = stmt.order_by(notifications.c.created_at.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [notification_from_row(r) for r in rows]

    def mark_read(self, notification_id: str) -> None:
        stmt = (
            sa.update(notifications)
            .where(notifications.c.id == notification_id)
            .values(status="read", read_at=now_utc().isoformat())
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

    def mark_dismissed(self, notification_id: str) -> None:
        stmt = (
            sa.update(notifications)
            .where(notifications.c.id == notification_id)
            .values(status="dismissed", dismissed_at=now_utc().isoformat())
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

    def count_unread(self, recipient_id: str) -> int:
        stmt = (
            sa.select(sa.func.count().label("cnt"))
            .select_from(notifications)
            .where(
                notifications.c.recipient_id == recipient_id,
                notifications.c.status == "unread",
                notifications.c.lifecycle == "ACTIVE",
            )
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _match_rules(self, event: Event) -> list[NotificationRule]:
        stmt = sa.select(notification_rules).where(
            notification_rules.c.lifecycle == "ACTIVE"
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        matched = []
        for row in rows:
            rule = rule_from_row(row)
            if self._rule_matches(rule, event):
                matched.append(rule)
        return matched

    @staticmethod
    def _rule_matches(rule: NotificationRule, event: Event) -> bool:
        if rule.event_types and event.event_type.value not in rule.event_types:
            return False
        if rule.target_types and event.target_type not in rule.target_types:
            return False
        if rule.scope_id is not None and event.scope_id != rule.scope_id:
            return False
        return True

    def _create_notification(
        self,
        *,
        recipient_id: str,
        title: str,
        body: str,
        channel: NotificationChannel,
        source_event_id: str | None = None,
        source_rule_id: str | None = None,
        scope_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> Notification:
        ts = now_utc()
        notification = Notification(
            id=generate_id(),
            recipient_id=recipient_id,
            title=title,
            body=body,
            channel=channel,
            status=NotificationStatus.UNREAD,
            created_at=ts,
            source_event_id=source_event_id,
            source_rule_id=source_rule_id,
            scope_id=scope_id,
            data=data or {},
        )
        stmt = sa.insert(notifications).values(
            id=notification.id,
            recipient_id=notification.recipient_id,
            title=notification.title,
            body=notification.body,
            channel=notification.channel.value,
            status=notification.status.value,
            created_at=notification.created_at.isoformat(),
            source_event_id=notification.source_event_id,
            source_rule_id=notification.source_rule_id,
            scope_id=notification.scope_id,
            data_json=json.dumps(notification.data),
            lifecycle=notification.lifecycle.name,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
        return notification
