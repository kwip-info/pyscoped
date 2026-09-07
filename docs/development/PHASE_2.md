# Phase 2 — enforcement and lifecycle coverage

Status: complete; advanced to phase 3. Initial restrictions below were subsequently
replaced by tested native bulk writes and scoped joins in phase 4.

Ensure scoped queryset construction and evaluation use the same explicit context,
including counts, existence, iterator/async paths, queryset combinations, and
subqueries. SQL scope predicates must precede pagination and aggregation. Avoid
loading a globally bounded list of object IDs as v1 did.

Support ordinary instance saves, partial updates, expressions, get/update-or-create,
and audited row-by-row queryset update in a single transaction. Reject bulk_create,
bulk_update, raw fixture saves, primary-key/scope updates, and unsafe joins. This is
an intentional correctness-first limit, with a documented loop/atomic alternative.
Do not silently provide bulk semantics with different signals/history behavior.

Require ScopedManager on every registered public manager in audit and enforcement
modes; preserve custom queryset methods through explicit composition. Validate
unsupported relation on_delete configurations. Existing relationships between scoped
models must share a scope; enforce on supported writes, detect unsafe traversals,
and document that historical inconsistencies need repair before enabling enforcement.

Tests: cached/lazy query escape, cross-context queryset combinations/subqueries,
pagination/aggregation, custom managers, update atomicity on later-row failure,
read-to-write alias routing, cascade denial rollback, deferred refresh, context
cleanup, async calls, bulk rejections, and registration/system checks.

Evidence (2026-09-07): 44 tests pass on Python 3.13 / Django 6.0.8 / SQLite.
Tests exposed an over-broad join guard that rejected valid FK prefetches; fixed
by checking active aliases while rejecting select_related explicitly. Cross-context
subqueries and queryset combinations reject before SQL execution. Async CRUD,
scope-filtered aggregation, cascade rollback, and multi-row audit failure pass.
PostgreSQL/version matrix and further edge-case review remain phase 4 gates.
