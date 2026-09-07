# PyScoped 2.0 migration and hosted-service retirement

PyScoped 2.0 is a free MIT Django library. It is a major rewrite, not a drop-in
replacement for the 1.x `scoped` object-store API. Install `pyscoped==2.0.0` and
import `pyscoped`. Django 5.2/6.0 are supported. There is no cloud account,
record ingestion, paid tier, or runtime license check.

## Hosted service

The kwip.tech v1 API, ingestion, dashboard, sign-in, and billing endpoints return
HTTP 410 Gone. Stop old sync agents and scheduled ingestion jobs. Retries cannot
restore the service. Existing PyScoped 1.x local data is not migrated or deleted
by this retirement. Historical platform data is retained privately for recovery;
the public repository contains source code, not production records.

## Existing Django applications

Keep your tables, primary keys, authentication, and membership checks. Add
`pyscoped` to INSTALLED_APPS and run migrations to add audit tables. Register one
existing model using its non-null scope field and an explicit audit field allowlist.
Use ScopedManager even in audit mode so bulk writes are audited. Start in audit
mode, supply explicit trusted actor/scope context, baseline existing records with
`pyscoped_backfill` (dry-run first), and test before enabling enforcement.
See [adoption](adoption.md) and [guarantees](guarantees.md).

Backfill records the current state; it cannot invent earlier events. The 1.x
universal object store, rules, and history require an application-specific export
and remodel. Keep a 1.x database backup and pinned environment during that work.
No automatic schema conversion, universal rollback, or deleted-row restoration is
provided. 1.x source remains in the SDK repository under `legacy/v1/` and old
published versions remain available for migration; hosted support has ended.
