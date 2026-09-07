# Guarantees and boundaries

This document describes PyScoped 2.0.0a1. It supersedes the universal invariants in
`legacy/v1/`. An alpha is available for review and integration testing, not a claim
of established production reliability or legal compliance.

## Enforcement boundary

The caller is trusted application code. It authenticates actors and authorizes
scope membership before entering context. All members admitted into a scope share
the library's scope-level read/write access. Additional roles, object permissions,
sharing rules, and membership revocation checks remain in the application.

An enforced model's scoped **root queryset** contains a predicate on the existing
scope column. Joins from that queryset into other enforced models receive additional
ON-clause scope predicates; outer joins retain parents with no visible related row. Supported materialization, aggregates, counts, and existence checks
retain that predicate. Querysets/subqueries cannot be evaluated under another
context, even if their earlier results are cached. Supported instance writes check
the persisted row and proposed scope before mutation. Normal scope changes fail.
A request context is an authorization snapshot; immediate revocation during a long
request/stream is not promised. Reauthorize at application boundaries as necessary.

This does not protect raw SQL/RawSQL expressions, manually constructed plain
QuerySets, direct use of `_base_manager` or other private APIs, custom methods that
bypass or override the guards, unmanaged external database writers, historical
migration model operations, or unregistered models. An object already returned to
application code cannot be made unreadable by a context change. FK and one-to-one descriptors between enforced models use scoped target queries
and recheck context even when the related object is cached. Relationships from
unregistered/audit-only models and direct use of Django's private base manager remain
outside this boundary. No database RLS or
sandboxing of arbitrary Python code is claimed.

## Mutation and audit contract

Supported mutations are ordinary model save (including partial and expression
updates), scoped queryset update, model/queryset deletion, and Django cascades.
Every registered write needs an actor context in both audit and enforcement modes.
Failure to write the audit event rolls back the application write. An enclosing
transaction can roll both back. Custom model save logic is wrapped transactionally;
external side effects still need application on_commit/outbox handling.

Queryset update, ordinary bulk_create, and bulk_update retain native Django SQL and
save-signal semantics: no custom save methods or save signals are invoked. They
lock/snapshot affected persisted rows and append per-row events in the same database
transaction. Affected rows are materialized. Queryset update accepts direct concrete-
field expressions; custom annotation aliases as update values are outside the initial
contract. Conflict-handling bulk inserts and duplicate bulk-update identities are
rejected rather than producing ambiguous history. These rules include async variants.
Skipped no-op `save(update_fields=[])` has Django's existing no-write behavior.
A save that writes unchanged values still records an attributed event.

Deletion follows Django's normal deletion semantics, with retained history. Scope-
mismatched cascades are rejected. Unsupported collector field updates (SET_NULL,
SET_DEFAULT, custom on_delete) and implicit M2M configurations fail checks.
PyScoped does not claim universal soft deletion or resurrection.

Audit snapshots include only explicitly selected concrete fields, using actual
persisted values after save. An allowlist avoids copying everything automatically;
it is not a secret detector. Snapshots are not encrypted by this package. Configure
application/database access, retention, backups, and encryption to fit your needs.
Actor IDs are references supplied by trusted application code, not copied users.

## History integrity and recovery

Each (model label, primary key) history has a revision counter and SHA-256 hash chain,
locked on the same database as the application row. History cannot be reassigned to
another scope when a PK is reused. Public event mutation helpers reject edits, but
administrative SQL remains able to change data. Verification checks revisions,
hashes, scope, and the retained head; it detects a removed tail when the head remains.
An administrator who replaces both the events and head can forge a new consistent
chain. Keep independent trusted checkpoints if that threat is in scope.

`history()` returns eagerly authorized events. The Django `AuditEvent` and `Resource`
models are internal administrative storage interfaces, not tenant-facing query APIs.
Never expose them directly through admin/API endpoints without your own authorization.

Backfill records a baseline observed now, commits per row, and is idempotent. It does
not claim old events or automatic reconciliation of untracked changes. Restore checks
the history, expected revision, and current selected values; applies selected scalar
fields to a live row; and appends a new restore event. It does not change PKs, scope,
relationships, unselected fields, deleted rows, or external state. A changed audit
field schema requires explicit history migration before restoration.

## Evidence and limits

The release record lists exact tested versions and results. No external adoption,
production audit, or performance result is inferred from unit tests. PostgreSQL is
the concurrent-write target; SQLite is the local development target. Other database
engines and future Django releases are not supported until verified.
