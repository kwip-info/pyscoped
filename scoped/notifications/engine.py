"""Notification engine — evaluate rules and generate notifications from events."""

from __future__ import annotations

import json
from typing import Any

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
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc


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
        self._backend.execute(
            "INSERT INTO notification_rules "
            "(id, name, owner_id, event_types_json, target_types_json, "
            "scope_id, recipient_ids_json, channel, title_template, "
            "body_template, created_at, lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rule.id, rule.name, rule.owner_id,
                json.dumps(rule.event_types),
                json.dumps(rule.target_types),
                rule.scope_id,
                json.dumps(rule.recipient_ids),
                rule.channel.value,
                rule.title_template,
                rule.body_template,
                rule.created_at.isoformat(),
                rule.lifecycle.name,
            ),
        )

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
        row = self._backend.fetch_one(
            "SELECT * FROM notification_rules WHERE id = ?", (rule_id,),
        )
        return rule_from_row(row) if row else None

    def list_rules(
        self,
        *,
        owner_id: str | None = None,
        active_only: bool = True,
    ) -> list[NotificationRule]:
        clauses: list[str] = []
        params: list[Any] = []
        if active_only:
            clauses.append("lifecycle = 'ACTIVE'")
        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        where = " AND ".join(clauses) if clauses else "1=1"
        rows = self._backend.fetch_all(
            f"SELECT * FROM notification_rules WHERE {where} ORDER BY created_at DESC",
            tuple(params),
        )
        return [rule_from_row(r) for r in rows]

    def archive_rule(self, rule_id: str) -> None:
        self._backend.execute(
            "UPDATE notification_rules SET lifecycle = 'ARCHIVED' WHERE id = ?",
            (rule_id,),
        )

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
        notifications: list[Notification] = []

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
                notifications.append(notification)

        return notifications

    # ------------------------------------------------------------------
    # Notification CRUD
    # ------------------------------------------------------------------

    def get_notification(self, notification_id: str) -> Notification | None:
        row = self._backend.fetch_one(
            "SELECT * FROM notifications WHERE id = ?", (notification_id,),
        )
        return notification_from_row(row) if row else None

    def list_notifications(
        self,
        *,
        recipient_id: str,
        status: NotificationStatus | None = None,
        limit: int = 100,
    ) -> list[Notification]:
        clauses = ["recipient_id = ?", "lifecycle = 'ACTIVE'"]
        params: list[Any] = [recipient_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        where = " AND ".join(clauses)
        rows = self._backend.fetch_all(
            f"SELECT * FROM notifications WHERE {where} ORDER BY created_at DESC LIMIT ?",
            tuple(params) + (limit,),
        )
        return [notification_from_row(r) for r in rows]

    def mark_read(self, notification_id: str) -> None:
        self._backend.execute(
            "UPDATE notifications SET status = 'read', read_at = ? WHERE id = ?",
            (now_utc().isoformat(), notification_id),
        )

    def mark_dismissed(self, notification_id: str) -> None:
        self._backend.execute(
            "UPDATE notifications SET status = 'dismissed', dismissed_at = ? WHERE id = ?",
            (now_utc().isoformat(), notification_id),
        )

    def count_unread(self, recipient_id: str) -> int:
        row = self._backend.fetch_one(
            "SELECT COUNT(*) as cnt FROM notifications "
            "WHERE recipient_id = ? AND status = 'unread' AND lifecycle = 'ACTIVE'",
            (recipient_id,),
        )
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _match_rules(self, event: Event) -> list[NotificationRule]:
        rows = self._backend.fetch_all(
            "SELECT * FROM notification_rules WHERE lifecycle = 'ACTIVE'",
        )
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
        self._backend.execute(
            "INSERT INTO notifications "
            "(id, recipient_id, title, body, channel, status, created_at, "
            "source_event_id, source_rule_id, scope_id, data_json, lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                notification.id, notification.recipient_id,
                notification.title, notification.body,
                notification.channel.value, notification.status.value,
                notification.created_at.isoformat(),
                notification.source_event_id, notification.source_rule_id,
                notification.scope_id, json.dumps(notification.data),
                notification.lifecycle.name,
            ),
        )
        return notification
