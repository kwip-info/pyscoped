---
title: "Scheduling & Jobs"
description: "Create cron and interval schedules, attach actions, and execute jobs through the pyscoped scheduling subsystem."
category: "Extensions"
---

# Scheduling & Jobs

pyscoped includes a lightweight scheduling and job-execution subsystem. It is
built from three cooperating components:

1. **Scheduler** -- defines recurring or one-shot schedules and the actions
   bound to them.
2. **process_due_actions** -- a bridge function that finds due actions, enqueues
   them into a job queue, and advances or archives the schedule.
3. **JobQueue** -- a persistent, pluggable queue that executes jobs via
   registered executor functions.

## Scheduler

The `Scheduler` manages two entity types: **schedules** (timing definitions)
and **actions** (work to perform when a schedule fires).

### Creating a schedule

A schedule can use either a cron expression or a fixed interval in seconds.

```python
from scoped.scheduling import Scheduler

scheduler = Scheduler(backend=storage)

# Cron-based schedule (every weekday at 09:00 UTC)
cron_sched = scheduler.create_schedule(
    name="weekday-morning",
    owner_id="user-42",
    cron_expression="0 9 * * 1-5",
)

# Interval-based schedule (every 300 seconds)
interval_sched = scheduler.create_schedule(
    name="five-minute-poll",
    owner_id="user-42",
    interval_seconds=300,
)
```

### Creating an action

An action describes _what_ should happen when the schedule fires. The
`action_type` is a string key that maps to a registered executor, and
`action_config` is an arbitrary dict passed to that executor at runtime.

```python
from datetime import datetime, timezone

action = scheduler.create_action(
    name="sync-inventory",
    owner_id="user-42",
    action_type="http_request",
    action_config={
        "method": "POST",
        "url": "https://api.internal/inventory/sync",
        "headers": {"Authorization": "Bearer {{secret:inv-token}}"},
    },
    next_run_at=datetime.now(timezone.utc),
    schedule_id=interval_sched["id"],
)
```

When `schedule_id` is provided the action recurs according to that schedule.
When omitted the action is treated as one-shot: it runs once and is archived
automatically.

### Querying due actions

`get_due_actions` returns all actions whose `next_run_at` is at or before
the given timestamp.

```python
due = scheduler.get_due_actions(as_of=datetime.now(timezone.utc))
```

### Advancing and archiving

After a recurring action fires, call `advance_action` to compute its next
run time based on the linked schedule.

```python
scheduler.advance_action(action["id"])
```

To permanently retire a schedule or action:

```python
scheduler.archive_schedule(cron_sched["id"])
scheduler.archive_action(action["id"])
```

Archived entities no longer appear in `get_due_actions` results.

## process_due_actions

`process_due_actions` is a convenience function that bridges the Scheduler
and the JobQueue. It performs three steps in a single call:

1. Queries the Scheduler for all due actions (as of now).
2. Enqueues each action into the provided JobQueue.
3. Advances recurring actions to their next run time, and archives one-shot
   actions.

```python
from scoped.scheduling import Scheduler, JobQueue, process_due_actions

scheduler = Scheduler(backend=storage)
queue = JobQueue(backend=storage)

# In your tick loop or cron runner:
enqueued = process_due_actions(scheduler, queue)
print(f"Enqueued {len(enqueued)} jobs")
```

This is the recommended way to drive the scheduling pipeline in production.
Call it on a fixed interval (e.g., every 30 seconds) from a background thread,
a system cron job, or a Kubernetes CronJob.

### Cron expression support

The Scheduler accepts an optional `cron_parser` callable for evaluating cron
expressions. Without one, cron-based schedules fall back to a 1-hour interval
placeholder and emit a `UserWarning`.

```python
# Example using croniter (pip install croniter)
from croniter import croniter

def parse_cron(expr: str, dt: datetime) -> datetime:
    return croniter(expr, dt).get_next(datetime)

scheduler = Scheduler(backend=storage, cron_parser=parse_cron)
```

Any callable with the signature `(cron_expression: str, current_time: datetime) -> datetime`
can be used. The framework does not bundle a cron parser to avoid the extra
dependency.

## JobQueue

The `JobQueue` provides persistent, ordered job execution with state tracking.

### Enqueuing jobs

You can enqueue jobs directly (bypassing the scheduler) or let
`process_due_actions` do it for you.

```python
queue = JobQueue(backend=storage)

job = queue.enqueue(
    name="one-off-report",
    action_type="generate_report",
    action_config={"report_id": "rpt-77", "format": "pdf"},
    owner_id="user-42",
)
```

### JobState

Every job passes through a well-defined lifecycle:

| State | Meaning |
|---|---|
| `QUEUED` | Waiting to be picked up |
| `RUNNING` | Currently executing |
| `COMPLETED` | Finished successfully |
| `FAILED` | Executor raised an exception |
| `CANCELLED` | Manually cancelled before execution |

### Executing jobs

`run_next` picks the oldest `QUEUED` job, transitions it to `RUNNING`, invokes
the registered executor, and marks it `COMPLETED` or `FAILED`.

```python
result = queue.run_next()
# result is the dict returned by the executor, or None if the queue is empty
```

`run_all` drains the queue, executing every `QUEUED` job in order:

```python
results = queue.run_all()
```

### Listing and cancelling

```python
# List jobs for an owner, optionally filtered by state
jobs = queue.list_jobs(owner_id="user-42", state=JobState.QUEUED)

# Cancel a queued job
queue.cancel_job(job["id"])
```

Cancellation only works on `QUEUED` jobs. Attempting to cancel a `RUNNING` or
terminal job raises `JobStateError`.

## Pluggable executor

The job queue dispatches work through executor functions. An executor receives
the `action_type` string and the `action_config` dict, and returns a result
dict.

```python
def http_executor(action_type: str, action_config: dict) -> dict:
    """Execute an HTTP request action."""
    import httpx

    resp = httpx.request(
        method=action_config["method"],
        url=action_config["url"],
        headers=action_config.get("headers", {}),
        json=action_config.get("body"),
    )
    return {"status_code": resp.status_code, "body": resp.text}


def report_executor(action_type: str, action_config: dict) -> dict:
    """Generate a PDF report."""
    pdf_bytes = render_pdf(action_config["report_id"], action_config["format"])
    return {"size_bytes": len(pdf_bytes)}
```

Register executors when constructing the queue:

```python
queue = JobQueue(
    backend=storage,
    executors={
        "http_request": http_executor,
        "generate_report": report_executor,
    },
)
```

If a job's `action_type` has no registered executor, `run_next` marks the job
as `FAILED` with an "unknown action_type" error.

## End-to-end example

```python
from datetime import datetime, timezone
from scoped.client import ScopedClient
from scoped.scheduling import (
    JobQueue,
    JobState,
    Scheduler,
    process_due_actions,
)

client = ScopedClient()
storage = client.storage

# --- Setup ---
scheduler = Scheduler(backend=storage)

schedule = scheduler.create_schedule(
    name="hourly-sync",
    owner_id="ops-team",
    interval_seconds=3600,
)

scheduler.create_action(
    name="sync-crm",
    owner_id="ops-team",
    action_type="http_request",
    action_config={
        "method": "POST",
        "url": "https://crm.internal/sync",
    },
    next_run_at=datetime.now(timezone.utc),
    schedule_id=schedule["id"],
)

# --- Tick (called every 30 s by your runner) ---
def tick():
    queue = JobQueue(
        backend=storage,
        executors={"http_request": http_executor},
    )
    enqueued = process_due_actions(scheduler, queue)
    results = queue.run_all()
    for r in results:
        print("Job result:", r)

tick()

# --- Ad-hoc inspection ---
queue = JobQueue(backend=storage)
failed = queue.list_jobs(owner_id="ops-team", state=JobState.FAILED)
for job in failed:
    print(job["name"], job["error"])
```

### Running with a background thread

For long-running services, a simple thread-based loop works well:

```python
import threading
import time

def scheduler_loop(interval=30):
    while True:
        try:
            tick()
        except Exception:
            logging.exception("Scheduler tick failed")
        time.sleep(interval)

thread = threading.Thread(target=scheduler_loop, daemon=True)
thread.start()
```

For production deployments, consider driving the tick from an external scheduler
(system cron, Kubernetes CronJob, or a task runner like Celery Beat) so that
scheduling survives process restarts.
