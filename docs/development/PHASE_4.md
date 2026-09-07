# Phase 4 — release hardening

Status: complete for the local alpha. The executable phase-3 example also passes.

Validate SQLite and PostgreSQL on Django 5.2 / 6.0. Exercise real PostgreSQL row
locks with concurrent updates and simultaneous baseline workers. Test write routing
and explicit database aliases. Check migration drift and configuration failures.
Run lint, Django checks, and package metadata validation.

Build wheel and sdist, inspect contents for legacy/cloud/billing code, then install
the built wheel into a clean environment and run the adoption example outside the
checkout. Validate sdist rebuild. CI gates must include PostgreSQL before packaging.
The public docs must describe the alpha support matrix, supported operations,
supported joins/bulk writes and unsupported conflict/M2M modes, trusted actor/membership boundary, raw SQL limitations,
history limitations, and a clear adoption and development path.

Pin kwip.tech to its existing 1.8.1 usage until its separate overhaul. Prepare an
explicit compatibility/cutover inventory; do not mutate that production application.
Publishing the alpha is a separate action and is not implied by passing local tests.

Evidence: all eight supported Python/Django combinations pass on SQLite (83 tests,
two expected PostgreSQL skips) and PostgreSQL 16.15 (85 tests). Ruff, system checks,
migration drift, isolated wheel installation, sdist-to-wheel build, Twine metadata,
and the existing-app demo pass. Distribution inspection excludes legacy source and
local caches/state. Remote CI and public publication were not performed. See
`RELEASE_READINESS.md` for the version matrix and remaining alpha limits.

## Adoption correction during hardening

The initial phase-2 rejection of ordinary joins and bulk writes is too restrictive
for the stated adoption outcome. Extend the candidate before closing this phase:
scope joined registered tables in SQL ON clauses (preserving outer-join semantics),
and support ordinary native bulk_create/bulk_update with transactional snapshots.
Keep conflict-handling bulk modes and duplicate bulk-update identities explicitly
unsupported until they have a precise tested contract. Re-run isolation, join,
bulk, concurrency, and artifact checks; update public docs to the final behavior.
