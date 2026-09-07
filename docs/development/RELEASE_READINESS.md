> Historical pre-release record. Stable release and cutover are complete;
> see RELEASE_2_0_0.md for final evidence.

# PyScoped 2.0.0a1 release readiness

Date: 2026-09-07. Owner: Trevor Ewert. Implementation: Codex.
Branch: `codex/django-overhaul`.

Status: locally validated development prerelease. Not published, deployed, or
represented as production-stable. This is a breaking Django-only release candidate
for integration evaluation, distributed under MIT with no service/account dependency.

## Implemented outcome

- Existing Django model registration/decorator and an optional field-free base class.
- Native scope predicates on root queries and joined enforced models, including
  outer joins, custom querysets, FK/one-to-one access, and prefetch.
- Explicit actor context; sync/async request, service, and streaming integration.
- Native queryset/bulk-write signal semantics with transactional per-row snapshots.
- Attributed save/delete/cascade history and same-database routing/rollback.
- Scope-restricted history, idempotent baseline, verification, and conflict-checked
  scalar restoration that appends a new event.
- Normal Django migrations; no initialization DDL, hosted sync, telemetry, or paid gates.

## Local validation

Each listed combination passes **83 tests on SQLite**, with two PostgreSQL-only
concurrency tests intentionally skipped; **85 tests pass on PostgreSQL 16.15**.

| Django | Python versions tested locally |
| --- | --- |
| 5.2.17 | 3.10.20, 3.11.14, 3.12.13, 3.13.6, 3.14.5 |
| 6.0.8 | 3.12.13, 3.13.6, 3.14.5 |

Coverage includes isolation before pagination/aggregation; protected inner/outer
joins over historically inconsistent rows; forward/reverse relation reads;
cross-context queries/subqueries; partial/expression writes; ordinary bulk writes
without save signals; atomic rollback on later audit failure; cascade denial;
database routing and explicit aliases; async CRUD/streaming; restoration conflicts
and tampering; and simultaneous PostgreSQL updates/baseline workers.

Ruff lint/format, Django system checks, and migration drift checks pass. The existing
application example keeps invoice ID 501 and its unselected private note unchanged
through baseline/update/restore. No network service is used by the demo.

The wheel and sdist are built with isolated build environments; metadata passes
Twine validation. Packaging smoke tests inspect contents, install the wheel into a
fresh virtual environment, verify import origin outside the checkout, run migrations,
and execute the adoption demo. `python -m build` builds the wheel from the sdist.
Distribution contents exclude `legacy/v1`, its 1.x cloud/platform code, local state,
and caches. Final files and SHA-256 checksums are in ignored `dist/`.

CI is configured for this same eight-combination SQLite/PostgreSQL matrix, with
PostgreSQL success required before artifact building. Remote CI has not been run;
no push or publication is claimed by this record.

## Explicit limits

The initial release excludes implicit M2M mutation tracking, multi-table inheritance,
proxy/composite-PK models, bulk conflict handling, duplicate bulk-update identities,
and update values derived from custom annotation aliases. Unsupported configurations
and operations must fail clearly; see the guarantees and adoption guide.

Application authentication, membership authorization, role/object permissions,
retention/encryption, and external side effects remain application responsibilities.
Raw SQL/private ORM APIs and unregistered/audit-only query roots do not become
enforcement boundaries. SQLite concurrent writers can receive lock errors; ordinary
database failures/deadlocks propagate for application retry. Bulk snapshots are
materialized, so realistic workloads need performance evaluation before production.

The alpha has no external-adoption or production-soak evidence. Stable release
promotion should follow review and an actual application integration trial, not a
renamed version number. The current user request authorizes the overhaul and release
preparation; package publication and production cutover remain separate actions.

## kwip.tech and rollback

kwip.tech remains pinned to 1.8.1. Its development editable requirement points at the
SDK checkout and must use a legacy checkout/source when working on that old app.
The full baseline is preserved under `legacy/v1/`; original Git commit:
`e3cf9486e45b216bed438dced1266193b15337e6`.

See `KWIP_TECH_CUTOVER.md` before changing the production site's SDK dependency,
authentication, billing, data, or routes. No production migration was performed.
