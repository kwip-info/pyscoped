"""Scheduler — manage scheduled actions and recurring schedules."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from scoped.registry.base import get_registry
from scoped.registry.kinds import RegistryKind
from scoped.registry.sqlite_store import SQLiteRegistryStore
from scoped.scheduling.models import (
    RecurringSchedule,
    ScheduledAction,
    schedule_from_row,
    scheduled_action_from_row,
)
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc


class Scheduler:
    """Manage scheduled actions and recurring schedules.

    The scheduler creates and queries scheduled actions. Evaluation
    (checking what's due) is done by :meth:`get_due_actions`.
    Actual execution is handled by :class:`JobQueue`.

    Parameters
    ----------
    backend:
        Storage backend for persistence.
    """

    def __init__(self, backend: StorageBackend, *, audit_writer: Any | None = None) -> None:
        self._backend = backend
        self._audit = audit_writer

    # ------------------------------------------------------------------
    # Recurring schedules
    # ------------------------------------------------------------------

    def create_schedule(
        self,
        *,
        name: str,
        owner_id: str,
        cron_expression: str | None = None,
        interval_seconds: int | None = None,
    ) -> RecurringSchedule:
        """Create a recurring schedule.

        Exactly one of ``cron_expression`` or ``interval_seconds`` must be provided.
        """
        if not cron_expression and not interval_seconds:
            raise ValueError("Either cron_expression or interval_seconds must be provided")
        if cron_expression and interval_seconds:
            raise ValueError("Only one of cron_expression or interval_seconds may be provided")

        schedule = RecurringSchedule(
            id=generate_id(),
            name=name,
            owner_id=owner_id,
            cron_expression=cron_expression,
            interval_seconds=interval_seconds,
            created_at=now_utc(),
        )
        self._backend.execute(
            "INSERT INTO recurring_schedules "
            "(id, name, owner_id, cron_expression, interval_seconds, created_at, lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                schedule.id, schedule.name, schedule.owner_id,
                schedule.cron_expression, schedule.interval_seconds,
                schedule.created_at.isoformat(), schedule.lifecycle.name,
            ),
        )

        # Auto-register (Invariant #1)
        try:
            reg = get_registry()
            entry = reg.register(
                kind=RegistryKind.SCHEDULE,
                namespace="scheduling",
                name=f"schedule:{schedule.id}",
                registered_by=owner_id,
            )
            SQLiteRegistryStore(self._backend).save_entry(entry)
        except Exception:
            pass

        if self._audit is not None:
            try:
                self._audit.record(
                    actor_id=owner_id,
                    action=ActionType.SCHEDULE_CREATE,
                    target_type="recurring_schedule",
                    target_id=schedule.id,
                    after_state={"name": name},
                )
            except Exception:
                pass

        return schedule

    def get_schedule(self, schedule_id: str) -> RecurringSchedule | None:
        row = self._backend.fetch_one(
            "SELECT * FROM recurring_schedules WHERE id = ?", (schedule_id,),
        )
        return schedule_from_row(row) if row else None

    def list_schedules(
        self,
        *,
        owner_id: str | None = None,
        active_only: bool = True,
    ) -> list[RecurringSchedule]:
        clauses: list[str] = []
        params: list[Any] = []
        if active_only:
            clauses.append("lifecycle = 'ACTIVE'")
        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        where = " AND ".join(clauses) if clauses else "1=1"
        rows = self._backend.fetch_all(
            f"SELECT * FROM recurring_schedules WHERE {where} ORDER BY created_at DESC",
            tuple(params),
        )
        return [schedule_from_row(r) for r in rows]

    def archive_schedule(self, schedule_id: str) -> None:
        self._backend.execute(
            "UPDATE recurring_schedules SET lifecycle = 'ARCHIVED' WHERE id = ?",
            (schedule_id,),
        )

        if self._audit is not None:
            try:
                self._audit.record(
                    actor_id="system",
                    action=ActionType.SCHEDULE_ARCHIVE,
                    target_type="recurring_schedule",
                    target_id=schedule_id,
                    before_state={"lifecycle": "ACTIVE"},
                    after_state={"lifecycle": "ARCHIVED"},
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Scheduled actions
    # ------------------------------------------------------------------

    def create_action(
        self,
        *,
        name: str,
        owner_id: str,
        action_type: str,
        action_config: dict[str, Any] | None = None,
        next_run_at: datetime,
        schedule_id: str | None = None,
        scope_id: str | None = None,
    ) -> ScheduledAction:
        """Create a scheduled action."""
        action = ScheduledAction(
            id=generate_id(),
            name=name,
            owner_id=owner_id,
            action_type=action_type,
            action_config=action_config or {},
            next_run_at=next_run_at,
            schedule_id=schedule_id,
            scope_id=scope_id,
            created_at=now_utc(),
        )
        self._backend.execute(
            "INSERT INTO scheduled_actions "
            "(id, name, owner_id, action_type, action_config_json, "
            "next_run_at, schedule_id, scope_id, created_at, lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action.id, action.name, action.owner_id,
                action.action_type, json.dumps(action.action_config),
                action.next_run_at.isoformat(),
                action.schedule_id, action.scope_id,
                action.created_at.isoformat(), action.lifecycle.name,
            ),
        )

        if self._audit is not None:
            try:
                self._audit.record(
                    actor_id=owner_id,
                    action=ActionType.CREATE,
                    target_type="scheduled_action",
                    target_id=action.id,
                    after_state={"name": name, "action_type": action_type},
                )
            except Exception:
                pass

        return action

    def get_action(self, action_id: str) -> ScheduledAction | None:
        row = self._backend.fetch_one(
            "SELECT * FROM scheduled_actions WHERE id = ?", (action_id,),
        )
        return scheduled_action_from_row(row) if row else None

    def list_actions(
        self,
        *,
        owner_id: str | None = None,
        active_only: bool = True,
    ) -> list[ScheduledAction]:
        clauses: list[str] = []
        params: list[Any] = []
        if active_only:
            clauses.append("lifecycle = 'ACTIVE'")
        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        where = " AND ".join(clauses) if clauses else "1=1"
        rows = self._backend.fetch_all(
            f"SELECT * FROM scheduled_actions WHERE {where} ORDER BY next_run_at ASC",
            tuple(params),
        )
        return [scheduled_action_from_row(r) for r in rows]

    def get_due_actions(self, as_of: datetime | None = None) -> list[ScheduledAction]:
        """Get all active scheduled actions whose next_run_at <= as_of."""
        ts = (as_of or now_utc()).isoformat()
        rows = self._backend.fetch_all(
            "SELECT * FROM scheduled_actions "
            "WHERE lifecycle = 'ACTIVE' AND next_run_at <= ? "
            "ORDER BY next_run_at ASC",
            (ts,),
        )
        return [scheduled_action_from_row(r) for r in rows]

    def advance_action(
        self,
        action_id: str,
        next_run_at: datetime,
    ) -> None:
        """Update the next_run_at for a recurring action after execution."""
        self._backend.execute(
            "UPDATE scheduled_actions SET next_run_at = ? WHERE id = ?",
            (next_run_at.isoformat(), action_id),
        )

    def process_due_actions(
        self,
        queue: Any,
        *,
        as_of: datetime | None = None,
    ) -> list[Any]:
        """Enqueue all due actions into a JobQueue and advance their schedules.

        This is the primary bridge between the Scheduler and the JobQueue.
        For each due action:
        1. Enqueues a Job via ``queue.enqueue()``
        2. If the action has a linked schedule with ``interval_seconds``,
           advances ``next_run_at`` by the interval
        3. If the action has no linked schedule (one-shot), archives it

        Args:
            queue: A ``JobQueue`` instance.
            as_of: Evaluation timestamp. Defaults to now.

        Returns:
            List of enqueued ``Job`` objects.
        """
        due = self.get_due_actions(as_of=as_of)
        jobs = []

        for action in due:
            job = queue.enqueue(
                name=action.name,
                action_type=action.action_type,
                action_config=action.action_config,
                owner_id=action.owner_id,
                scheduled_action_id=action.id,
                scope_id=action.scope_id,
            )
            jobs.append(job)

            # Advance or archive
            if action.schedule_id:
                schedule = self.get_schedule(action.schedule_id)
                if schedule and schedule.interval_seconds:
                    next_run = action.next_run_at + timedelta(
                        seconds=schedule.interval_seconds,
                    )
                    self.advance_action(action.id, next_run)
                else:
                    # Cron schedules need external cron parsing — advance
                    # by a placeholder; real cron parsing is application-level
                    self.advance_action(
                        action.id,
                        action.next_run_at + timedelta(hours=1),
                    )
            else:
                # One-shot action — archive after enqueue
                self.archive_action(action.id)

            if self._audit is not None:
                try:
                    self._audit.record(
                        actor_id="system",
                        action=ActionType.JOB_ENQUEUE,
                        target_type="job",
                        target_id=job.id,
                        after_state={
                            "action_type": action.action_type,
                            "scheduled_action_id": action.id,
                        },
                    )
                except Exception:
                    pass

        return jobs

    def archive_action(self, action_id: str) -> None:
        self._backend.execute(
            "UPDATE scheduled_actions SET lifecycle = 'ARCHIVED' WHERE id = ?",
            (action_id,),
        )

        if self._audit is not None:
            try:
                self._audit.record(
                    actor_id="system",
                    action=ActionType.LIFECYCLE_CHANGE,
                    target_type="scheduled_action",
                    target_id=action_id,
                    before_state={"lifecycle": "ACTIVE"},
                    after_state={"lifecycle": "ARCHIVED"},
                )
            except Exception:
                pass
