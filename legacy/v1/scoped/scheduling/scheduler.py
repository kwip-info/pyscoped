"""Scheduler — manage scheduled actions and recurring schedules."""

from __future__ import annotations

import json
import warnings
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa

from scoped.registry.base import get_registry
from scoped.registry.kinds import RegistryKind
from scoped.registry.sqlite_store import SQLiteRegistryStore
from scoped.scheduling.models import (
    RecurringSchedule,
    ScheduledAction,
    schedule_from_row,
    scheduled_action_from_row,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import recurring_schedules, scheduled_actions
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc
from scoped._stability import experimental


@experimental()
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

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
        cron_parser: Callable[[str, datetime], datetime] | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer
        self._cron_parser = cron_parser

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
        stmt = sa.insert(recurring_schedules).values(
            id=schedule.id,
            name=schedule.name,
            owner_id=schedule.owner_id,
            cron_expression=schedule.cron_expression,
            interval_seconds=schedule.interval_seconds,
            created_at=schedule.created_at.isoformat(),
            lifecycle=schedule.lifecycle.name,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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
        stmt = sa.select(recurring_schedules).where(recurring_schedules.c.id == schedule_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return schedule_from_row(row) if row else None

    def list_schedules(
        self,
        *,
        owner_id: str | None = None,
        active_only: bool = True,
    ) -> list[RecurringSchedule]:
        stmt = sa.select(recurring_schedules)
        if active_only:
            stmt = stmt.where(recurring_schedules.c.lifecycle == "ACTIVE")
        if owner_id is not None:
            stmt = stmt.where(recurring_schedules.c.owner_id == owner_id)
        stmt = stmt.order_by(recurring_schedules.c.created_at.desc())
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [schedule_from_row(r) for r in rows]

    def archive_schedule(self, schedule_id: str) -> None:
        stmt = (
            sa.update(recurring_schedules)
            .where(recurring_schedules.c.id == schedule_id)
            .values(lifecycle="ARCHIVED")
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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
        stmt = sa.insert(scheduled_actions).values(
            id=action.id,
            name=action.name,
            owner_id=action.owner_id,
            action_type=action.action_type,
            action_config_json=json.dumps(action.action_config),
            next_run_at=action.next_run_at.isoformat(),
            schedule_id=action.schedule_id,
            scope_id=action.scope_id,
            created_at=action.created_at.isoformat(),
            lifecycle=action.lifecycle.name,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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
        stmt = sa.select(scheduled_actions).where(scheduled_actions.c.id == action_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return scheduled_action_from_row(row) if row else None

    def list_actions(
        self,
        *,
        owner_id: str | None = None,
        active_only: bool = True,
    ) -> list[ScheduledAction]:
        stmt = sa.select(scheduled_actions)
        if active_only:
            stmt = stmt.where(scheduled_actions.c.lifecycle == "ACTIVE")
        if owner_id is not None:
            stmt = stmt.where(scheduled_actions.c.owner_id == owner_id)
        stmt = stmt.order_by(scheduled_actions.c.next_run_at.asc())
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [scheduled_action_from_row(r) for r in rows]

    def get_due_actions(self, as_of: datetime | None = None) -> list[ScheduledAction]:
        """Get all active scheduled actions whose next_run_at <= as_of."""
        ts = (as_of or now_utc()).isoformat()
        stmt = (
            sa.select(scheduled_actions)
            .where(
                scheduled_actions.c.lifecycle == "ACTIVE",
                scheduled_actions.c.next_run_at <= ts,
            )
            .order_by(scheduled_actions.c.next_run_at.asc())
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [scheduled_action_from_row(r) for r in rows]

    def advance_action(
        self,
        action_id: str,
        next_run_at: datetime,
    ) -> None:
        """Update the next_run_at for a recurring action after execution."""
        stmt = (
            sa.update(scheduled_actions)
            .where(scheduled_actions.c.id == action_id)
            .values(next_run_at=next_run_at.isoformat())
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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
                elif schedule and schedule.cron_expression:
                    if self._cron_parser is not None:
                        next_run = self._cron_parser(
                            schedule.cron_expression,
                            action.next_run_at,
                        )
                    else:
                        warnings.warn(
                            f"No cron_parser provided to Scheduler. "
                            f"Falling back to 1-hour interval for "
                            f"cron expression '{schedule.cron_expression}'. "
                            f"Pass a cron_parser callable to "
                            f"Scheduler.__init__() to enable real "
                            f"cron scheduling.",
                            stacklevel=2,
                        )
                        next_run = action.next_run_at + timedelta(hours=1)
                    self.advance_action(action.id, next_run)
                else:
                    # Schedule has neither interval nor cron — archive
                    self.archive_action(action.id)
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
        stmt = (
            sa.update(scheduled_actions)
            .where(scheduled_actions.c.id == action_id)
            .values(lifecycle="ARCHIVED")
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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
