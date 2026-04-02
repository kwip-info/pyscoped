---
title: "Operations Guide"
description: "Run migrations, configure sync agents, instrument with OpenTelemetry, set up health checks, and manage backups for pyscoped deployments."
category: "Operations"
---

# Operations Guide

This guide covers the day-to-day operational concerns of running pyscoped in
production: schema migrations, remote sync, structured logging, distributed
tracing, health checks, and backup strategies.

## Migrations

pyscoped manages its own database schema through a built-in migration runner.
Migrations are idempotent, ordered, and checksum-validated.

### MigrationRunner

```python
from scoped.migrations import MigrationRunner

runner = MigrationRunner(backend=storage)
```

### Discovering and applying

```python
# List all available migrations (bundled with the package)
available = runner.discover()

# Apply every unapplied migration in order
runner.apply_all()

# Check current state
status = runner.get_status()
for m in status:
    print(m["id"], m["description"], m["applied"], m["checksum_ok"])
```

### Rolling back

```python
# Undo the most recently applied migration
runner.rollback_last()

# Roll back to a specific migration (inclusive -- that migration stays applied)
runner.rollback_to("m0008")
```

### Checksum validation

Every migration file has a SHA-256 checksum recorded at apply time.
`get_status()` re-computes the checksum and flags any mismatch in the
`checksum_ok` field. A mismatch means the migration source was modified after
it was applied -- this is always a red flag and should be investigated before
applying further migrations.

### Migration inventory

| ID | Description |
|---|---|
| `m0001` | Core schema -- scopes, objects, memberships |
| `m0002` | Rules and access-control tables |
| `m0003` | Audit log and trace chain |
| `m0004` | Temporal versioning tables |
| `m0005` | Environments, deployments, gates |
| `m0006` | Secrets vault and rotation tracking |
| `m0007` | Plugins and hooks |
| `m0008` | Connectors, policies, traffic |
| `m0009` | Marketplace listings |
| `m0010` | Contracts and templates |
| `m0011` | Events, subscriptions, webhooks, notifications |
| `m0012` | Schedules, actions, jobs |
| `m0013` | Sync state and federation |
| `m0014` | Audit sequence UNIQUE constraint (multi-process safety) |

## Sync agent

The sync agent replicates data between a local pyscoped instance and a remote
pyscoped server. It runs as a background process inside your application.

### SyncConfig

```python
from scoped.sync import SyncConfig

config = SyncConfig(
    base_url="https://scoped.prod.internal",
    interval_seconds=30,
    batch_size=100,
    max_retries=5,
    request_timeout=10,
)
```

| Parameter | Default | Description |
|---|---|---|
| `base_url` | (required) | Remote pyscoped server URL |
| `interval_seconds` | `30` | Seconds between sync cycles |
| `batch_size` | `100` | Max records per sync batch |
| `max_retries` | `3` | Retries per failed batch |
| `request_timeout` | `10` | HTTP timeout in seconds |

### Controlling the agent

```python
from scoped.sync import SyncAgent

agent = SyncAgent(backend=storage, config=config)

agent.start_sync()       # Begin background sync loop
agent.pause_sync()       # Pause without stopping the thread
agent.resume_sync()      # Resume after pause
agent.stop_sync()        # Gracefully shut down

status = agent.sync_status()
# {"state": "running", "last_sync_at": "...", "records_synced": 4210}
```

### Verification

After syncing, you can verify data integrity between local and remote:

```python
report = agent.verify_sync()
# {
#   "total_local": 4210,
#   "total_remote": 4210,
#   "mismatches": [],
#   "missing_local": [],
#   "missing_remote": [],
# }
```

If mismatches are found, the report lists the affected record IDs so you can
investigate and reconcile manually.

## Structured logging

pyscoped uses structured JSON logging throughout. Every log entry includes a
timestamp, level, module, message, and optional context fields.

### Getting a logger

```python
from scoped.logging import get_logger

logger = get_logger("my_module")

logger.info("Processing batch", batch_size=100, scope_id="scope-1")
logger.warning("Slow query detected", duration_ms=1200)
logger.error("Sync failed", error="ConnectionTimeout", retries=3)
```

Output (single JSON line, formatted here for readability):

```json
{
  "timestamp": "2026-03-15T14:22:01.003Z",
  "level": "INFO",
  "module": "my_module",
  "message": "Processing batch",
  "batch_size": 100,
  "scope_id": "scope-1",
  "principal_id": "user-42"
}
```

### Audit logging

The `audit()` method emits a log entry at the `AUDIT` level, which is always
enabled regardless of the configured log level.

```python
logger.audit(
    "Access granted",
    principal_id="user-42",
    resource="doc-99",
    action="read",
)
```

### Configuration

Set the log level via the `SCOPED_LOG_LEVEL` environment variable:

```bash
export SCOPED_LOG_LEVEL=DEBUG    # DEBUG, INFO, WARNING, ERROR, AUDIT
```

The default level is `INFO`. The `AUDIT` level is always emitted regardless of
this setting.

### Auto-enrichment

When a principal context is active, every log entry is automatically enriched
with the `principal_id` field. You do not need to pass it manually.

## OpenTelemetry instrumentation

pyscoped ships with first-class OpenTelemetry support. A single call
instruments 21 core operations with spans and attributes.

### Enabling instrumentation

```python
from scoped import Client
from scoped.telemetry import instrument

client = Client()
instrument(client)
```

After this call, every instrumented operation creates an OpenTelemetry span
with the following attributes:

- `scoped.operation` -- the operation name (e.g., `create_scope`)
- `scoped.scope_id` -- scope ID when applicable
- `scoped.principal_id` -- acting principal
- `scoped.object_type` -- object type when applicable
- `scoped.result` -- `ok` or `error`

### Instrumented operations

The 21 instrumented operations span the full API surface:

| Category | Operations |
|---|---|
| Scopes | `create_scope`, `get_scope`, `modify_scope`, `dissolve_scope` |
| Objects | `create_object`, `get_object`, `update_object`, `delete_object` |
| Membership | `add_member`, `remove_member` |
| Rules | `create_rule`, `evaluate_access` |
| Audit | `append_audit`, `query_audit` |
| Temporal | `get_version`, `rollback` |
| Environments | `create_environment`, `promote` |
| Secrets | `create_secret`, `get_secret`, `rotate_secret` |
| Connectors | `sync_object` |
| Sync | `sync_batch` |

### Exporter configuration

pyscoped does not bundle an exporter -- use the standard OpenTelemetry SDK to
configure your preferred backend (Jaeger, Zipkin, OTLP, etc.):

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))

from opentelemetry import trace
trace.set_tracer_provider(provider)

# Now instrument pyscoped -- spans will flow to your OTLP backend
from scoped.telemetry import instrument
instrument(client)
```

## Health checks

pyscoped exposes health-check endpoints through its web-framework integrations
and the MCP server.

### FastAPI

```python
from fastapi import FastAPI
from scoped.integrations.fastapi import mount_health

app = FastAPI()
mount_health(app, backend=storage)
# GET /scoped/health -> {"status": "ok", "backend": "sqlite", "migrations": "up_to_date"}
```

### Flask

```python
from flask import Flask
from scoped.integrations.flask import mount_health

app = Flask(__name__)
mount_health(app, backend=storage)
# GET /scoped/health -> same payload
```

### MCP

The MCP server registers a `health_check` tool that returns the same payload
as the HTTP endpoints, enabling health monitoring from MCP-compatible clients.

### Response format

```json
{
  "status": "ok",
  "backend": "sqlite",
  "migrations": "up_to_date",
  "pending_migrations": 0,
  "sync_state": "running",
  "last_sync_at": "2026-03-15T14:22:01Z"
}
```

If any check fails, `status` changes to `"degraded"` or `"error"` with details
in an `errors` array.

## Backup considerations

### SQLite

For SQLite backends, the database is a single file. The simplest backup
strategy is a file copy while the application is idle, or using SQLite's
built-in `.backup` command:

```bash
sqlite3 /var/data/scoped.db ".backup /backups/scoped-$(date +%F).db"
```

For zero-downtime backups, enable WAL mode (pyscoped does this by default) and
use the `VACUUM INTO` command:

```sql
VACUUM INTO '/backups/scoped-snapshot.db';
```

### PostgreSQL

Use `pg_dump` for logical backups:

```bash
pg_dump -Fc -d scoped_production > /backups/scoped-$(date +%F).dump
```

For point-in-time recovery, configure continuous WAL archiving with your
PostgreSQL deployment.

### Sync state table

The `_sync_state` table is co-located with your main data and is included in
any full backup. This table tracks the high-water mark for the sync agent, so
restoring from backup without it would cause the agent to re-sync from the
beginning. Always ensure it is part of your backup and restore procedure.

### Restore checklist

1. Stop the application and sync agent.
2. Restore the database file (SQLite) or run `pg_restore` (PostgreSQL).
3. Run `MigrationRunner.get_status()` to verify checksum integrity.
4. Start the application.
5. Start the sync agent -- it will resume from the restored high-water mark.
