"""Migration 0010: Scheduling & Jobs tables.

Layer 16 — recurring_schedules, scheduled_actions, jobs.
"""

from __future__ import annotations

from scoped.storage.interface import StorageBackend


def up(backend: StorageBackend) -> None:
    backend.execute("""
        CREATE TABLE IF NOT EXISTS recurring_schedules (
            id                  TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            owner_id            TEXT NOT NULL,
            cron_expression     TEXT,
            interval_seconds    INTEGER,
            created_at          TEXT NOT NULL,
            lifecycle           TEXT NOT NULL DEFAULT 'ACTIVE',
            FOREIGN KEY (owner_id) REFERENCES principals(id)
        )
    """)
    backend.execute("CREATE INDEX IF NOT EXISTS idx_schedules_owner ON recurring_schedules(owner_id)")
    backend.execute("CREATE INDEX IF NOT EXISTS idx_schedules_lifecycle ON recurring_schedules(lifecycle)")

    backend.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_actions (
            id                  TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            owner_id            TEXT NOT NULL,
            action_type         TEXT NOT NULL,
            action_config_json  TEXT NOT NULL DEFAULT '{}',
            next_run_at         TEXT NOT NULL,
            schedule_id         TEXT REFERENCES recurring_schedules(id),
            scope_id            TEXT,
            created_at          TEXT NOT NULL,
            lifecycle           TEXT NOT NULL DEFAULT 'ACTIVE',
            FOREIGN KEY (owner_id) REFERENCES principals(id)
        )
    """)
    backend.execute("CREATE INDEX IF NOT EXISTS idx_actions_owner ON scheduled_actions(owner_id)")
    backend.execute("CREATE INDEX IF NOT EXISTS idx_actions_next_run ON scheduled_actions(next_run_at)")
    backend.execute("CREATE INDEX IF NOT EXISTS idx_actions_lifecycle ON scheduled_actions(lifecycle)")

    backend.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id                  TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            action_type         TEXT NOT NULL,
            action_config_json  TEXT NOT NULL DEFAULT '{}',
            owner_id            TEXT NOT NULL,
            state               TEXT NOT NULL DEFAULT 'queued',
            created_at          TEXT NOT NULL,
            started_at          TEXT,
            completed_at        TEXT,
            result_json         TEXT NOT NULL DEFAULT '{}',
            error_message       TEXT,
            scheduled_action_id TEXT REFERENCES scheduled_actions(id),
            scope_id            TEXT,
            lifecycle           TEXT NOT NULL DEFAULT 'ACTIVE',
            FOREIGN KEY (owner_id) REFERENCES principals(id)
        )
    """)
    backend.execute("CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner_id)")
    backend.execute("CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state)")
    backend.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at)")


def down(backend: StorageBackend) -> None:
    backend.execute("DROP TABLE IF EXISTS jobs")
    backend.execute("DROP TABLE IF EXISTS scheduled_actions")
    backend.execute("DROP TABLE IF EXISTS recurring_schedules")
