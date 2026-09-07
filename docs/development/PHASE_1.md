# Phase 1 — Django foundation

Status: complete; advanced to phase 2.

Implement a standalone `pyscoped` Django app with migrations (no startup DDL), an
explicit ContextVar actor/scope context, and per-model registration. The optional
base model adds no columns. Explicit field allowlists avoid copying secrets by
default. Keep scope and identity references as existing application keys.

Store per-resource revisions and hash-linked before/after audit events in the same
database as the application row. Serialize persisted values using Django's JSON
encoder. Lock existing application rows and audit heads during mutations; require
normal database conflict handling for concurrent creation. A chain is tamper-evident,
not proof against a database administrator rewriting the entire chain.

Wrap model saves inside a transaction, read the actual stored result (including
partial updates and expressions), and reject raw fixture saves. Audit delete signals
inside the collector transaction so queryset/cascade deletion cannot silently skip
history. Fail unsupported relation configurations during Django checks.

Tests: unchanged app schema, real model save/update/delete, UUID/integer primary
keys, actual actor attribution, missing context, partial updates, nested transaction
rollback, audit write failure, and no database queries during app initialization.

Evidence (2026-09-07): 14 database tests pass on Python 3.13 / Django 6.0.8 /
SQLite. Django system checks and migration drift checks pass. Audit failure and
outer transaction rollback leave no application row or audit head behind. Found
and fixed denied-delete transaction poisoning with explicit savepoints. PostgreSQL
and supported-version validation remain release gates in phase 4.
