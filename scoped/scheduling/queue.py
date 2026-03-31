"""Job queue — execute jobs, track status, record results."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from scoped.scheduling.models import Job, JobState, job_from_row
from scoped.storage.interface import StorageBackend
from scoped.types import Lifecycle, generate_id, now_utc


# Type alias for job executors
JobExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]
"""(action_type, action_config) -> result_dict"""


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
        self._backend.execute(
            "INSERT INTO jobs "
            "(id, name, action_type, action_config_json, owner_id, state, "
            "created_at, scheduled_action_id, scope_id, lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job.id, job.name, job.action_type,
                json.dumps(job.action_config), job.owner_id,
                job.state.value, job.created_at.isoformat(),
                job.scheduled_action_id, job.scope_id,
                job.lifecycle.name,
            ),
        )
        return job

    # ------------------------------------------------------------------
    # Job execution
    # ------------------------------------------------------------------

    def run_next(self) -> Job | None:
        """Run the next queued job. Returns the completed/failed Job, or None."""
        row = self._backend.fetch_one(
            "SELECT * FROM jobs WHERE state = 'queued' ORDER BY created_at ASC LIMIT 1",
        )
        if row is None:
            return None
        return self._run_job(row)

    def run_all(self) -> list[Job]:
        """Run all queued jobs in order. Returns list of completed/failed Jobs."""
        rows = self._backend.fetch_all(
            "SELECT * FROM jobs WHERE state = 'queued' ORDER BY created_at ASC",
        )
        return [self._run_job(r) for r in rows]

    # ------------------------------------------------------------------
    # Job queries
    # ------------------------------------------------------------------

    def get_job(self, job_id: str) -> Job | None:
        row = self._backend.fetch_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return job_from_row(row) if row else None

    def list_jobs(
        self,
        *,
        owner_id: str | None = None,
        state: JobState | None = None,
        limit: int = 100,
    ) -> list[Job]:
        clauses: list[str] = []
        params: list[Any] = []
        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        if state is not None:
            clauses.append("state = ?")
            params.append(state.value)
        where = " AND ".join(clauses) if clauses else "1=1"
        rows = self._backend.fetch_all(
            f"SELECT * FROM jobs WHERE {where} ORDER BY created_at DESC LIMIT ?",
            tuple(params) + (limit,),
        )
        return [job_from_row(r) for r in rows]

    def count_jobs(self, *, state: JobState | None = None) -> int:
        if state is not None:
            row = self._backend.fetch_one(
                "SELECT COUNT(*) as cnt FROM jobs WHERE state = ?", (state.value,),
            )
        else:
            row = self._backend.fetch_one("SELECT COUNT(*) as cnt FROM jobs")
        return row["cnt"] if row else 0

    def cancel_job(self, job_id: str) -> None:
        """Cancel a queued job."""
        self._backend.execute(
            "UPDATE jobs SET state = 'cancelled' WHERE id = ? AND state = 'queued'",
            (job_id,),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_job(self, row: dict[str, Any]) -> Job:
        job_id = row["id"]
        action_type = row["action_type"]
        config = json.loads(row["action_config_json"]) if row.get("action_config_json") else {}

        started_at = now_utc()
        self._backend.execute(
            "UPDATE jobs SET state = 'running', started_at = ? WHERE id = ?",
            (started_at.isoformat(), job_id),
        )

        try:
            result = self._executor(action_type, config)
            completed_at = now_utc()
            self._backend.execute(
                "UPDATE jobs SET state = 'completed', completed_at = ?, result_json = ? WHERE id = ?",
                (completed_at.isoformat(), json.dumps(result), job_id),
            )
            row_updated = self._backend.fetch_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
            return job_from_row(row_updated)
        except Exception as exc:
            completed_at = now_utc()
            self._backend.execute(
                "UPDATE jobs SET state = 'failed', completed_at = ?, error_message = ? WHERE id = ?",
                (completed_at.isoformat(), str(exc), job_id),
            )
            row_updated = self._backend.fetch_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
            return job_from_row(row_updated)

    @staticmethod
    def _default_executor(action_type: str, action_config: dict[str, Any]) -> dict[str, Any]:
        """Default no-op executor for testing."""
        return {"status": "ok", "action_type": action_type}
