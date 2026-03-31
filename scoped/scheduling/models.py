"""Scheduling and job data models.

ScheduledAction defines *what* should run and *when*.
RecurringSchedule defines cron-like repetition patterns.
Job is an executable unit of work with lifecycle tracking.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from scoped.types import Lifecycle, generate_id, now_utc


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class JobState(Enum):
    """Lifecycle of a job execution."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# ScheduledAction
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ScheduledAction:
    """A registered action that fires at a specified time or interval.

    ``action_type`` identifies what to do (e.g., 'run_report', 'rotate_secret').
    ``action_config`` provides parameters for the action.
    ``next_run_at`` is when the action should next execute.
    ``schedule_id`` optionally links to a RecurringSchedule for repetition.
    """

    id: str
    name: str
    owner_id: str
    action_type: str
    action_config: dict[str, Any]
    next_run_at: datetime
    schedule_id: str | None
    scope_id: str | None
    created_at: datetime
    lifecycle: Lifecycle = Lifecycle.ACTIVE


# ---------------------------------------------------------------------------
# RecurringSchedule
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RecurringSchedule:
    """A cron-like repetition pattern.

    ``cron_expression`` uses standard 5-field cron syntax:
    minute hour day_of_month month day_of_week

    ``interval_seconds`` is a simpler alternative: fire every N seconds.
    One of cron_expression or interval_seconds must be set.
    """

    id: str
    name: str
    owner_id: str
    cron_expression: str | None
    interval_seconds: int | None
    created_at: datetime
    lifecycle: Lifecycle = Lifecycle.ACTIVE


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Job:
    """An executable unit of work with lifecycle tracking.

    Jobs are created from scheduled actions or directly.
    They track execution state, timing, and results.
    """

    id: str
    name: str
    action_type: str
    action_config: dict[str, Any]
    owner_id: str
    state: JobState
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    scheduled_action_id: str | None = None
    scope_id: str | None = None
    lifecycle: Lifecycle = Lifecycle.ACTIVE


# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------

def schedule_from_row(row: dict[str, Any]) -> RecurringSchedule:
    return RecurringSchedule(
        id=row["id"],
        name=row["name"],
        owner_id=row["owner_id"],
        cron_expression=row.get("cron_expression"),
        interval_seconds=row.get("interval_seconds"),
        created_at=datetime.fromisoformat(row["created_at"]),
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
    )


def scheduled_action_from_row(row: dict[str, Any]) -> ScheduledAction:
    return ScheduledAction(
        id=row["id"],
        name=row["name"],
        owner_id=row["owner_id"],
        action_type=row["action_type"],
        action_config=json.loads(row["action_config_json"]) if row.get("action_config_json") else {},
        next_run_at=datetime.fromisoformat(row["next_run_at"]),
        schedule_id=row.get("schedule_id"),
        scope_id=row.get("scope_id"),
        created_at=datetime.fromisoformat(row["created_at"]),
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
    )


def job_from_row(row: dict[str, Any]) -> Job:
    return Job(
        id=row["id"],
        name=row["name"],
        action_type=row["action_type"],
        action_config=json.loads(row["action_config_json"]) if row.get("action_config_json") else {},
        owner_id=row["owner_id"],
        state=JobState(row["state"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        started_at=datetime.fromisoformat(row["started_at"]) if row.get("started_at") else None,
        completed_at=datetime.fromisoformat(row["completed_at"]) if row.get("completed_at") else None,
        result=json.loads(row["result_json"]) if row.get("result_json") else {},
        error_message=row.get("error_message"),
        scheduled_action_id=row.get("scheduled_action_id"),
        scope_id=row.get("scope_id"),
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
    )
