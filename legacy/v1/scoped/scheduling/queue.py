"""Job queue — execute jobs, track status, record results."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import sqlalchemy as sa

from scoped.scheduling.models import Job, JobState, job_from_row
from scoped.storage._query import compile_for
from scoped.storage._schema import jobs
from scoped.storage.interface import StorageBackend
from scoped.types import Lifecycle, generate_id, now_utc
from scoped._stability import experimental


# Type alias for job executors
JobExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]
"""(action_type, action_config) -> result_dict"""


@experimental()
class JobQueue:
    """Execute jobs, track status, and record results.

    Parameters
    ----------
    backend:
        Storage backend for persistence.
    executor:
        Optional function that runs the actual work.
        Signature: ``(action_type, action_config) -> result_dict``.
        If not provided, a no-op executor is used.
    """

    def __init__(
        self,
        backend: StorageBackend,
        *,
        executor: JobExecutor | None = None,
    ) -> None:
        self._backend = backend
        self._executor = executor or self._default_executor

    # ------------------------------------------------------------------
    # Job creation
    # ------------------------------------------------------------------

    def enqueue(
        self,
        *,
        name: str,
        action_type: str,
        action_config: dict[str, Any] | None = None,
        owner_id: str,
        scheduled_action_id: str | None = None,
        scope_id: str | None = None,
    ) -> Job:
        """Create a new job in QUEUED state."""
        job = Job(
            id=generate_id(),
            name=name,
            action_type=action_type,
            action_config=action_config or {},
            owner_id=owner_id,
            state=JobState.QUEUED,
            created_at=now_utc(),
            scheduled_action_id=scheduled_action_id,
            scope_id=scope_id,
        )
        stmt = sa.insert(jobs).values(
            id=job.id,
            name=job.name,
            action_type=job.action_type,
            action_config_json=json.dumps(job.action_config),
            owner_id=job.owner_id,
            state=job.state.value,
            created_at=job.created_at.isoformat(),
            scheduled_action_id=job.scheduled_action_id,
            scope_id=job.scope_id,
            lifecycle=job.lifecycle.name,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
        return job

    # ------------------------------------------------------------------
    # Job execution
    # ------------------------------------------------------------------

    def run_next(self) -> Job | None:
        """Run the next queued job. Returns the completed/failed Job, or None."""
        stmt = (
            sa.select(jobs)
            .where(jobs.c.state == "queued")
            .order_by(jobs.c.created_at.asc())
            .limit(1)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            return None
        return self._run_job(row)

    def run_all(self) -> list[Job]:
        """Run all queued jobs in order. Returns list of completed/failed Jobs."""
        stmt = (
            sa.select(jobs)
            .where(jobs.c.state == "queued")
            .order_by(jobs.c.created_at.asc())
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [self._run_job(r) for r in rows]

    # ------------------------------------------------------------------
    # Job queries
    # ------------------------------------------------------------------

    def get_job(self, job_id: str) -> Job | None:
        stmt = sa.select(jobs).where(jobs.c.id == job_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return job_from_row(row) if row else None

    def list_jobs(
        self,
        *,
        owner_id: str | None = None,
        state: JobState | None = None,
        limit: int = 100,
    ) -> list[Job]:
        stmt = sa.select(jobs)
        if owner_id is not None:
            stmt = stmt.where(jobs.c.owner_id == owner_id)
        if state is not None:
            stmt = stmt.where(jobs.c.state == state.value)
        stmt = stmt.order_by(jobs.c.created_at.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [job_from_row(r) for r in rows]

    def count_jobs(self, *, state: JobState | None = None) -> int:
        stmt = sa.select(sa.func.count().label("cnt")).select_from(jobs)
        if state is not None:
            stmt = stmt.where(jobs.c.state == state.value)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return row["cnt"] if row else 0

    def cancel_job(self, job_id: str) -> None:
        """Cancel a queued job."""
        stmt = (
            sa.update(jobs)
            .where(jobs.c.id == job_id, jobs.c.state == "queued")
            .values(state="cancelled")
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_job(self, row: dict[str, Any]) -> Job:
        job_id = row["id"]
        action_type = row["action_type"]
        config = json.loads(row["action_config_json"]) if row.get("action_config_json") else {}

        started_at = now_utc()
        start_stmt = (
            sa.update(jobs)
            .where(jobs.c.id == job_id)
            .values(state="running", started_at=started_at.isoformat())
        )
        sql_s, params_s = compile_for(start_stmt, self._backend.dialect)
        self._backend.execute(sql_s, params_s)

        try:
            result = self._executor(action_type, config)
            completed_at = now_utc()
            done_stmt = (
                sa.update(jobs)
                .where(jobs.c.id == job_id)
                .values(
                    state="completed",
                    completed_at=completed_at.isoformat(),
                    result_json=json.dumps(result),
                )
            )
            sql_d, params_d = compile_for(done_stmt, self._backend.dialect)
            self._backend.execute(sql_d, params_d)

            get_stmt = sa.select(jobs).where(jobs.c.id == job_id)
            sql_g, params_g = compile_for(get_stmt, self._backend.dialect)
            row_updated = self._backend.fetch_one(sql_g, params_g)
            return job_from_row(row_updated)
        except Exception as exc:
            completed_at = now_utc()
            fail_stmt = (
                sa.update(jobs)
                .where(jobs.c.id == job_id)
                .values(
                    state="failed",
                    completed_at=completed_at.isoformat(),
                    error_message=str(exc),
                )
            )
            sql_f, params_f = compile_for(fail_stmt, self._backend.dialect)
            self._backend.execute(sql_f, params_f)

            get_stmt = sa.select(jobs).where(jobs.c.id == job_id)
            sql_g, params_g = compile_for(get_stmt, self._backend.dialect)
            row_updated = self._backend.fetch_one(sql_g, params_g)
            return job_from_row(row_updated)

    @staticmethod
    def _default_executor(action_type: str, action_config: dict[str, Any]) -> dict[str, Any]:
        """Default no-op executor for testing."""
        return {"status": "ok", "action_type": action_type}
