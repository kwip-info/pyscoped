"""Tests for Layer 16: Scheduling & Jobs."""

from __future__ import annotations

from datetime import timedelta

import pytest

from scoped.scheduling.models import (
    JobState,
    job_from_row,
    schedule_from_row,
    scheduled_action_from_row,
)
from scoped.scheduling.queue import JobQueue
from scoped.scheduling.scheduler import Scheduler
from scoped.types import Lifecycle, generate_id, now_utc


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
# Row mappers
# ===========================================================================

class TestRowMappers:
    def test_schedule_from_row(self):
        ts = now_utc()
        row = {
            "id": "s1", "name": "hourly", "owner_id": "u1",
            "cron_expression": "0 * * * *", "interval_seconds": None,
            "created_at": ts.isoformat(), "lifecycle": "ACTIVE",
        }
        s = schedule_from_row(row)
        assert s.name == "hourly"
        assert s.cron_expression == "0 * * * *"

    def test_job_from_row(self):
        ts = now_utc()
        row = {
            "id": "j1", "name": "test job", "action_type": "report",
            "action_config_json": '{"format": "csv"}', "owner_id": "u1",
            "state": "completed", "created_at": ts.isoformat(),
            "started_at": ts.isoformat(), "completed_at": ts.isoformat(),
            "result_json": '{"rows": 100}', "error_message": None,
            "scheduled_action_id": None, "scope_id": None,
            "lifecycle": "ACTIVE",
        }
        j = job_from_row(row)
        assert j.state == JobState.COMPLETED
        assert j.result == {"rows": 100}
        assert j.action_config == {"format": "csv"}


# ===========================================================================
# Recurring schedules
# ===========================================================================

class TestRecurringSchedules:
    def test_create_cron(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        sched = Scheduler(sqlite_backend)

        schedule = sched.create_schedule(
            name="every hour", owner_id=user, cron_expression="0 * * * *",
        )

        assert schedule.name == "every hour"
        assert schedule.cron_expression == "0 * * * *"
        assert schedule.interval_seconds is None

    def test_create_interval(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        sched = Scheduler(sqlite_backend)

        schedule = sched.create_schedule(
            name="every 5 min", owner_id=user, interval_seconds=300,
        )

        assert schedule.interval_seconds == 300
        assert schedule.cron_expression is None

    def test_create_requires_one(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        sched = Scheduler(sqlite_backend)

        with pytest.raises(ValueError, match="Either"):
            sched.create_schedule(name="bad", owner_id=user)

    def test_create_rejects_both(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        sched = Scheduler(sqlite_backend)

        with pytest.raises(ValueError, match="Only one"):
            sched.create_schedule(
                name="bad", owner_id=user,
                cron_expression="* * * * *", interval_seconds=60,
            )

    def test_get_schedule(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        sched = Scheduler(sqlite_backend)

        schedule = sched.create_schedule(name="test", owner_id=user, interval_seconds=60)
        fetched = sched.get_schedule(schedule.id)

        assert fetched is not None
        assert fetched.id == schedule.id

    def test_list_schedules(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        sched = Scheduler(sqlite_backend)

        sched.create_schedule(name="a", owner_id=user, interval_seconds=60)
        sched.create_schedule(name="b", owner_id=user, interval_seconds=120)

        schedules = sched.list_schedules(owner_id=user)
        assert len(schedules) == 2

    def test_archive_schedule(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        sched = Scheduler(sqlite_backend)

        schedule = sched.create_schedule(name="test", owner_id=user, interval_seconds=60)
        sched.archive_schedule(schedule.id)

        schedules = sched.list_schedules(owner_id=user)
        assert len(schedules) == 0


# ===========================================================================
# Scheduled actions
# ===========================================================================

class TestScheduledActions:
    def test_create_action(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        sched = Scheduler(sqlite_backend)

        action = sched.create_action(
            name="run report",
            owner_id=user,
            action_type="generate_report",
            action_config={"format": "csv"},
            next_run_at=now_utc() + timedelta(hours=1),
        )

        assert action.action_type == "generate_report"
        assert action.action_config == {"format": "csv"}

    def test_create_with_schedule(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        sched = Scheduler(sqlite_backend)

        schedule = sched.create_schedule(name="hourly", owner_id=user, interval_seconds=3600)
        action = sched.create_action(
            name="hourly report",
            owner_id=user,
            action_type="report",
            next_run_at=now_utc(),
            schedule_id=schedule.id,
        )

        assert action.schedule_id == schedule.id

    def test_get_due_actions(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        sched = Scheduler(sqlite_backend)

        # Past action — due
        sched.create_action(
            name="due", owner_id=user, action_type="task",
            next_run_at=now_utc() - timedelta(minutes=5),
        )
        # Future action — not due
        sched.create_action(
            name="future", owner_id=user, action_type="task",
            next_run_at=now_utc() + timedelta(hours=1),
        )

        due = sched.get_due_actions()
        assert len(due) == 1
        assert due[0].name == "due"

    def test_advance_action(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        sched = Scheduler(sqlite_backend)

        action = sched.create_action(
            name="test", owner_id=user, action_type="task",
            next_run_at=now_utc() - timedelta(minutes=5),
        )

        next_time = now_utc() + timedelta(hours=1)
        sched.advance_action(action.id, next_time)

        updated = sched.get_action(action.id)
        assert updated.next_run_at >= next_time - timedelta(seconds=1)

    def test_list_actions(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        sched = Scheduler(sqlite_backend)

        sched.create_action(name="a", owner_id=user, action_type="t", next_run_at=now_utc())
        sched.create_action(name="b", owner_id=user, action_type="t", next_run_at=now_utc())

        actions = sched.list_actions(owner_id=user)
        assert len(actions) == 2

    def test_archive_action(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        sched = Scheduler(sqlite_backend)

        action = sched.create_action(
            name="test", owner_id=user, action_type="t", next_run_at=now_utc(),
        )
        sched.archive_action(action.id)

        actions = sched.list_actions(owner_id=user)
        assert len(actions) == 0


# ===========================================================================
# Job queue — enqueue & run
# ===========================================================================

class TestJobQueue:
    def test_enqueue(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        queue = JobQueue(sqlite_backend)

        job = queue.enqueue(
            name="test job", action_type="report",
            action_config={"format": "pdf"}, owner_id=user,
        )

        assert job.state == JobState.QUEUED
        assert job.action_type == "report"

    def test_run_next(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        queue = JobQueue(sqlite_backend)

        queue.enqueue(name="job1", action_type="task", owner_id=user)

        result = queue.run_next()

        assert result is not None
        assert result.state == JobState.COMPLETED
        assert result.started_at is not None
        assert result.completed_at is not None

    def test_run_next_empty(self, sqlite_backend):
        queue = JobQueue(sqlite_backend)
        assert queue.run_next() is None

    def test_run_all(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        queue = JobQueue(sqlite_backend)

        queue.enqueue(name="j1", action_type="t", owner_id=user)
        queue.enqueue(name="j2", action_type="t", owner_id=user)
        queue.enqueue(name="j3", action_type="t", owner_id=user)

        results = queue.run_all()

        assert len(results) == 3
        assert all(j.state == JobState.COMPLETED for j in results)

    def test_custom_executor(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)

        def my_executor(action_type, config):
            return {"computed": config.get("x", 0) * 2}

        queue = JobQueue(sqlite_backend, executor=my_executor)

        queue.enqueue(
            name="compute", action_type="multiply",
            action_config={"x": 21}, owner_id=user,
        )

        result = queue.run_next()
        assert result.result == {"computed": 42}

    def test_executor_failure(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)

        def failing_executor(action_type, config):
            raise RuntimeError("boom")

        queue = JobQueue(sqlite_backend, executor=failing_executor)

        queue.enqueue(name="bad", action_type="fail", owner_id=user)

        result = queue.run_next()
        assert result.state == JobState.FAILED
        assert "boom" in result.error_message


# ===========================================================================
# Job queue — queries
# ===========================================================================

class TestJobQueries:
    def test_get_job(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        queue = JobQueue(sqlite_backend)

        job = queue.enqueue(name="test", action_type="t", owner_id=user)
        fetched = queue.get_job(job.id)

        assert fetched is not None
        assert fetched.id == job.id

    def test_list_by_state(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        queue = JobQueue(sqlite_backend)

        queue.enqueue(name="j1", action_type="t", owner_id=user)
        queue.enqueue(name="j2", action_type="t", owner_id=user)
        queue.run_next()  # completes j1

        queued = queue.list_jobs(state=JobState.QUEUED)
        completed = queue.list_jobs(state=JobState.COMPLETED)

        assert len(queued) == 1
        assert len(completed) == 1

    def test_count_jobs(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        queue = JobQueue(sqlite_backend)

        queue.enqueue(name="j1", action_type="t", owner_id=user)
        queue.enqueue(name="j2", action_type="t", owner_id=user)

        assert queue.count_jobs() == 2
        assert queue.count_jobs(state=JobState.QUEUED) == 2
        assert queue.count_jobs(state=JobState.COMPLETED) == 0

    def test_cancel_job(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        queue = JobQueue(sqlite_backend)

        job = queue.enqueue(name="cancel me", action_type="t", owner_id=user)
        queue.cancel_job(job.id)

        fetched = queue.get_job(job.id)
        assert fetched.state == JobState.CANCELLED


# ===========================================================================
# Integration: Scheduler → JobQueue
# ===========================================================================

class TestSchedulerJobIntegration:
    def test_due_action_creates_job(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        sched = Scheduler(sqlite_backend)
        queue = JobQueue(sqlite_backend)

        action = sched.create_action(
            name="due task", owner_id=user,
            action_type="generate_report",
            action_config={"format": "csv"},
            next_run_at=now_utc() - timedelta(minutes=1),
        )

        due = sched.get_due_actions()
        for a in due:
            queue.enqueue(
                name=a.name, action_type=a.action_type,
                action_config=a.action_config, owner_id=a.owner_id,
                scheduled_action_id=a.id,
            )

        jobs = queue.list_jobs(owner_id=user)
        assert len(jobs) == 1
        assert jobs[0].scheduled_action_id == action.id
        assert jobs[0].action_config == {"format": "csv"}

    def test_recurring_advance(self, sqlite_backend):
        user = _setup_principal(sqlite_backend)
        sched = Scheduler(sqlite_backend)
        queue = JobQueue(sqlite_backend)

        schedule = sched.create_schedule(
            name="every 5 min", owner_id=user, interval_seconds=300,
        )
        action = sched.create_action(
            name="recurring", owner_id=user, action_type="check",
            next_run_at=now_utc() - timedelta(minutes=1),
            schedule_id=schedule.id,
        )

        # Process due, advance
        due = sched.get_due_actions()
        for a in due:
            queue.enqueue(
                name=a.name, action_type=a.action_type,
                owner_id=a.owner_id, scheduled_action_id=a.id,
            )
            sched.advance_action(a.id, now_utc() + timedelta(seconds=300))

        # No longer due
        assert len(sched.get_due_actions()) == 0
