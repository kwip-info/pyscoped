# PyScoped 2.0 development plan

Owner: Trevor Ewert. Implementer: Codex. Started: 2026-09-07.
Status: phases 0–5 complete. Stable 2.0.0 published and the hosted-service cutover verified; see RELEASE_2_0_0.md.

## Outcome

An existing Django application can adopt tenant scoping and transactional audit
history one model at a time, retaining its tables, primary keys, auth, and business
logic. Entirely MIT, free, local, and independent of kwip.tech. The distribution
imports as `pyscoped`; 1.x's `scoped` API is a deliberately separate legacy product.

The 1.8.1 source, documentation, and tests are frozen in `legacy/v1/`, excluded
from distribution. Baseline: `e3cf9486e45b216bed438dced1266193b15337e6`.

## Release contract

- Django 5.2 LTS and 6.0; Python combinations supported by those Django versions.
  SQLite for development and PostgreSQL for transactional/concurrent use.
- Register existing models with a decorator or registration function. An optional
  abstract base supplies the manager without adding application columns.
- Use an existing non-null tenant/owner field as the scope key. No copied auth
  directory, universal JSON object store, or replacement application database.
- `audit` mode leaves reads unchanged; `enforce` mode requires a scoped manager
  and filters root querysets. Every registered write requires an explicit actor.
- Missing or mismatched context fails closed in enforcement mode. Never impersonate
  the record owner when the real actor is absent. No trusted identity headers.
- Save/update/delete audit records use the application's database transaction and
  selected persisted fields. Scope movement is not an ordinary update.
- Ordinary bulk writes and joins into enforced models preserve Django semantics
  with transactional snapshots and joined-table scope predicates. Unsupported
  conflict/relationship operations fail explicitly, with guidance;
  arbitrary SQL, historical migration models, private ORM APIs, already materialized
  data, and unregistered models are outside library enforcement.
- An actor/scope context is supplied by trusted application code after membership
  authorization. This library does not authenticate users or infer memberships.
- Backfill records a truthful baseline, is idempotent, and supports a dry run.
- History restoration is explicit, limited to selected scalar fields, audited as
  a new mutation, and conflict-checked. No universal rollback or legal compliance claim.
- Application deletion follows Django semantics; audit history is retained. No
  promise that ordinary rows or external effects can always be restored.

## Phases and gates

| Phase | Scope | Gate | Status |
| --- | --- | --- | --- |
| 0 | Contract, source isolation, dependency/cutover inventory | Written scope and legacy baseline | Complete |
| 1 | Native Django app, migrations, actor/scope context, model registration, audit storage | Existing model saves; missing identity fails; rollback atomicity tests | Complete |
| 2 | Scoped querysets and lifecycle coverage | Cross-scope read/write, bulk, partial update, delete, async, and manager checks | Complete |
| 3 | Adoption, history, request/service integration | Idempotent baseline, restoration conflicts, trusted context and existing-app example | Complete |
| 4 | Release hardening and public docs | Django/version and PostgreSQL checks, wheel/sdist install tests, accurate docs | Complete |

Before implementing each phase, expand its scope and tests in the corresponding
phase file. Record evidence and remaining gaps before marking a gate complete.
An alpha version indicates development, not a production-ready public release.
Do not advance a gate merely because code exists or earlier tests pass.

## Scope exclusions

No FastAPI, Flask, MCP, federation, marketplace, secrets vault, scheduling, deployment
engine, billing, license enforcement, cloud sync, or management-plane reimplementation.
No kwip.tech deployment changes in this SDK task. 1.8.1 remains its existing pin.
First prove adoption and guarantees; extend only from an evidenced use case.

## Release/cutover expectations

Prepare a validated 2.0 prerelease locally. Publication is a distinct recorded action.
The kwip.tech overhaul needs its own inventory of 1.x principal/scope/rule usages,
data backup, replacement behavior, staged migration, cutover, and rollback rehearsal.
No automated 1.x schema translation or production cutover is claimed here.

## Completion checkpoint — 2026-09-07

Prepared the free/MIT Django 2.0.0a1 wheel and sdist. All eight supported Python/Django
combinations pass locally on SQLite and PostgreSQL (83/85 tests respectively).
Ruff, migration checks, package metadata, isolated wheel installation, and the
existing-application demo pass. The original 463 tracked files are preserved
byte-for-byte in `legacy/v1/`. See `RELEASE_READINESS.md` for exact evidence and limits.

Stable promotion is a later decision after review and a real application trial.
No package publication, remote CI run, or kwip.tech cutover is claimed by this milestone.

## Phase 5 authorization

The later explicit user request authorizes stable publication and the kwip.tech
cutover. See PHASE_5_STABLE_RELEASE.md; earlier prerelease-only boundaries above
are historical and superseded for this phase.

## Stable completion

Phase 5 completed 2026-09-07. See RELEASE_2_0_0.md for immutable CI, publication,
artifact hashes, and existing-application integration evidence. Earlier alpha-only
scope and expectations above describe the historical phase 0–4 checkpoint.
