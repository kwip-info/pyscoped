# A1: Schema Migration System

**Extends:** Storage

## Purpose

Without migrations, upgrading Scoped versions is destructive. The migration system provides versioned, ordered schema evolution with forward (`up`) and backward (`down`) functions, a version stamp table to track what's been applied, and a runner to apply pending migrations.

## Core Concepts

### Migration

A Python file in `scoped/storage/migrations/versions/` with a numeric prefix, an `up(backend)` function, and a `down(backend)` function. Migrations are applied in filename order.

### MigrationRunner

Discovers all migration files, compares against the `scoped_migrations` stamp table, and applies pending migrations in order. Supports:

- `apply_all()` — run all pending migrations forward
- `rollback(target_version)` — roll back to a specific version
- `get_current_version()` — read the latest applied migration

### Version Stamp Table

```sql
CREATE TABLE IF NOT EXISTS scoped_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
```

Each applied migration records its version string and timestamp.

## Files

```
scoped/storage/
    migrations/
        __init__.py
        runner.py              # MigrationRunner
        registry.py            # Version tracking
        base.py                # BaseMigration interface
        versions/
            m0001_initial_schema.py
            m0002_contracts.py
            m0003_blobs.py
            m0004_scope_settings.py
            m0005_search_index.py
            m0006_templates.py
            m0007_tiering_archival.py
            m0008_events_webhooks.py
            m0009_notifications.py
            m0010_scheduling.py
            m0011_sync_state.py
            m0012_composite_indexes.py
            m0013_row_level_security.py
            m0014_audit_sequence_unique.py
```

## Usage

```python
from scoped.storage.migrations.runner import MigrationRunner
from scoped.storage.sa_sqlite import SASQLiteBackend

backend = SASQLiteBackend("my.db")
runner = MigrationRunner(backend)
runner.apply_all()
print(runner.get_current_version())
```

## Invariants

1. Migrations are applied in strict filename order.
2. Each migration is applied at most once (tracked by version stamp).
3. Every migration must have both `up()` and `down()` functions.
4. The stamp table is created automatically if it doesn't exist.
