# Layer 16: Scheduling & Jobs

## Purpose

Time-based actions. Without scheduling, everything in Scoped is reactive — triggered by a user or an event. Scheduling adds the ability to **do things at a specified time or on a recurring basis**: rotate secrets, auto-discard idle environments, run compliance checks on a schedule, execute recurring deployments.

## Dependencies

- **Layer 5 (Rules)** — who can schedule what, where
- **Layer 6 (Audit)** — job execution is traced
- **Layer 8 (Environments)** — jobs can target environments
- **Layer 9 (Flow)** — scheduled stage transitions

## Core Concepts

### RecurringSchedule

A reusable time pattern — cron expression or fixed interval.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `name` | Human-readable label ("Daily cleanup", "Hourly sync") |
| `owner_id` | Who created this schedule |
| `cron_expression` | Cron pattern (e.g., `0 2 * * *` for daily at 2am) |
| `interval_seconds` | Alternative: fixed interval in seconds |

Exactly one of `cron_expression` or `interval_seconds` should be set.

### ScheduledAction

A concrete action that fires at a specific time, optionally linked to a recurring schedule.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `name` | Human-readable label |
| `owner_id` | Who scheduled this action |
| `action_type` | What kind of action to perform |
| `action_config` | Parameters for the action (JSON) |
| `next_run_at` | When this action should next fire |
| `schedule_id` | Optional link to a recurring schedule |
| `scope_id` | Optional scope context |

For recurring actions, `next_run_at` is advanced after each execution using `advance_action()`.

### Job

An executable unit of work with lifecycle state.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `name` | Human-readable label |
| `action_type` | What kind of work |
| `action_config` | Parameters (JSON) |
| `owner_id` | Who owns this job |
| `state` | `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED` |
| `started_at` | When execution began |
| `completed_at` | When execution finished |
| `result` | Output data on success (JSON) |
| `error_message` | Error details on failure |
| `scheduled_action_id` | Optional link to the action that spawned this job |
| `scope_id` | Optional scope context |

### Job States

```
QUEUED → RUNNING → COMPLETED
                 → FAILED
       → CANCELLED
```

## Architecture

### Scheduler

Manages recurring schedules and scheduled actions. The scheduler doesn't execute — it defines **when** things should happen.

```python
scheduler = Scheduler(backend)

# Create a recurring schedule
schedule = scheduler.create_schedule(
    name="Nightly Cleanup",
    owner_id=admin.id,
    cron_expression="0 2 * * *",
)

# Create a scheduled action
action = scheduler.create_action(
    name="Purge expired environments",
    owner_id=admin.id,
    action_type="environment_cleanup",
    action_config={"max_age_hours": 24},
    next_run_at=next_2am,
    schedule_id=schedule.id,
    scope_id=org_scope.id,
)

# Query due actions
due = scheduler.get_due_actions()

# Advance to next run after execution
scheduler.advance_action(action.id, next_run_at=next_next_2am)
```

### JobQueue

Executes jobs. Uses a pluggable `JobExecutor` callable — the default is a no-op for testing; applications provide real executors.

```python
queue = JobQueue(backend, executor=my_executor)

# Enqueue a job
job = queue.enqueue(
    name="Cleanup expired environments",
    action_type="environment_cleanup",
    action_config={"max_age_hours": 24},
    owner_id=admin.id,
    scheduled_action_id=action.id,
)

# Execute the next queued job
completed_job = queue.run_next()

# Execute all queued jobs
all_completed = queue.run_all()

# Query and manage jobs
jobs = queue.list_jobs(state=JobState.FAILED)
queue.cancel_job(job.id)
count = queue.count_jobs(state=JobState.QUEUED)
```

The `JobExecutor` signature:
```python
def my_executor(action_type: str, action_config: dict) -> dict:
    """Execute a job and return result data."""
    ...
```

## Key Files

```
scoped/scheduling/
    __init__.py          # Package exports
    models.py            # ScheduledAction, RecurringSchedule, Job, JobState, enums
    scheduler.py         # Scheduler — schedule and action management
    queue.py             # JobQueue — job execution and lifecycle
```

## SQL Tables

- `recurring_schedules` — reusable time patterns
- `scheduled_actions` — concrete scheduled actions with `next_run_at`
- `jobs` — job records with state, result, and error tracking
- `job_executions` — execution history

## Invariants

1. **Jobs are scoped.** Every job has an `owner_id` and optional `scope_id`.
2. **Execution is traced.** Job state transitions are recorded.
3. **Schedules are declarative.** The scheduler defines when; the queue executes.
4. **Executors are pluggable.** Applications provide their own execution logic.
