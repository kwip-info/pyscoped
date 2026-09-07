# Adopt PyScoped in an existing Django application

## 1. Select one model and its existing scope

Choose a concrete model with an existing non-null organization, tenant, or owner
column. Scope keys may be strings, integers, UUIDs, or a ForeignKey to an existing
model. The scope field must not itself be the primary key. Index this field in your
own schema for realistic query workloads; PyScoped adds no application columns or
indexes implicitly. Actor and scope representations must fit 255 characters.

Use a field allowlist for audit snapshots. ForeignKey snapshots store the referenced
key; they do not serialize the related object. Decimal/UUID/date values use Django
JSON encoding. Files/blobs and arbitrary Python objects need an application-specific
representation, not an assumption that everything is JSON serializable.

## 2. Add registration and a manager

The decorator in the README works on an ordinary `models.Model`. It wraps the
existing save lifecycle, keeping custom save methods in the same transaction. A
custom method that intentionally bypasses Django's normal save lifecycle is outside
this contract.

For an existing queryset class, compose explicitly:

```python
from pyscoped.query import ScopedManager, ScopedQuerySet

class ScopedInvoiceQuerySet(ScopedQuerySet, ExistingInvoiceQuerySet):
    pass

# In your model:
objects = ScopedManager.from_queryset(ScopedInvoiceQuerySet)()
```

Review custom methods for direct SQL, bulk operations, and private ORM usage. Methods
that override guarded operations must call `super()` and preserve the contract.
Custom querysets must inherit `ScopedQuerySet`; inheriting only `models.QuerySet`
is not sufficient. Every public manager must use the scoped queryset machinery.

If modifying the base class is convenient, `ScopedModel` supplies `objects` without
adding columns. You still register its concrete subclass. Multi-table inheritance,
proxy models, nullable scope keys, implicit many-to-many fields, and relation
`SET_NULL`/`SET_DEFAULT`/custom on_delete updates are outside initial support.
Use explicit registered relationship models and PROTECT/RESTRICT or intentional
application updates instead. Run `manage.py check` before serving traffic.
Composite primary keys are outside initial support; ordinary integer, UUID, and
string primary keys are retained. With an FK `to_field`, supply the referenced field's
key as context scope, rather than assuming that reference is always the related PK.

`register(Invoice, scope_field="organization", fields=[...], mode="audit")` in your
app's `ready()` is equivalent to the decorator. Registration has no database access.
It must happen before the first query or write. The same options may be registered
repeatedly; conflicting registrations fail.

## 3. Attach actual identity

For HTTP, keep your current authentication stack. Configure a resolver that receives
the request, verifies current membership, and returns `ScopeContext(actor=user,
scope=organization.pk)`. Anonymous/public requests may return `None`; they cannot
perform enforced reads or registered writes without context. No header fallback
is provided. Put `ScopedMiddleware` after authentication and session middleware.
The example includes a resolver using ordinary Django users and a membership table.

For scripts and background jobs:

```python
with scope(actor="nightly-billing-job", scope=organization_id,
           reason="Monthly billing", request_id=job_id):
    Invoice.objects.filter(status="draft").update(status="ready")
```

Record a real job/service actor, not the owner of whichever record is being changed.
For existing functions:

```python
from pyscoped import ScopeContext
from pyscoped.integration import in_scope

@in_scope(lambda user, org, invoice_id: authorized_context(user, org))
def approve_invoice(user, org, invoice_id):
    # authorized_context checks membership and returns ScopeContext(user, org.pk).
    Invoice.objects.filter(pk=invoice_id).update(status="approved")
```

Resolvers and decorated functions can be sync or async. This decorator attaches
context; audit events come from registered model mutations inside the function.
It does not automatically undo an email, external API call, or file write.
Generator-function decorators are rejected; enter scope inside a generator or use
the middleware's streaming support instead.

## 4. Baseline current records

The `pyscoped_backfill` command defaults to preview, accepts `--database`,
`--batch-size`, and `--reason`, and requires explicit `--actor` and `--scope`.
Run it separately for each authorized scope, even in audit mode. Applying commits
one row at a time, locks the row, and skips existing histories. A partially completed
run can be resumed. Dry-run counts describe a point-in-time observation, not a lock
on future writes. Audit field changes and untracked prior mutations require explicit
reconciliation; rerunning baseline never overwrites history.

Check relationship integrity before enforcement. PyScoped checks same-scope FKs to
other enforced models on supported writes, but does not retroactively fix corrupt
historical relationships or replace database constraints.

## 5. Move from audit to enforcement

Start with `mode="audit"` if read behavior must remain unchanged during integration.
Both modes require context for writes, restrict unsupported mutation paths, and use
the same transaction machinery. Audit mode does not enforce tenant access on reads
or normal writes; the existing application authorization remains responsible.
History retrieval/restoration is always scope-restricted.

Set `mode="enforce"` in source and restart the application once callers provide
context and use the supported query paths. This switch does not transform data.
It changes query/write behavior, so exercise application tests, tasks, admin, and
error paths first. Django admin is not automatically made multi-tenant by installing
PyScoped; supply and authorize scope before its model access and test its joins.

For related data, use normal `select_related`, related fields, or `Prefetch`. Joins
from an enforced queryset add scope predicates to joined enforced tables in the
SQL ON clause, preserving outer joins. Forward FK and one-to-one descriptors between
enforced models use scoped target queries; explicit prefetch querysets also work.
Joins from unregistered/audit-only roots do not acquire automatic enforcement.
Avoid passing lazy querysets between scope contexts; evaluate them where constructed. Streaming middleware re-enters the same
context for each chunk and clears it before yielding to the caller.

## Transactions and deployment

Migrate PyScoped's tables on every database alias holding registered models. Events
and their resource heads are explicitly written to the same alias as the application
row. Routers must allow those tables on that database; cross-database transactions
are not simulated. Queryset update and bulk_update lock affected rows in PK order, run native SQL
mutations, and capture per-row persisted snapshots. Ordinary bulk_create captures
the inserted identities and records each result. These operations preserve native
Django bulk/queryset signal semantics: they do not invoke custom save hooks or save
signals. Conflict-handling inserts and duplicate bulk-update IDs are rejected.
Queryset update supports direct concrete-field expressions; updating from a custom
annotation alias is outside the initial contract. Benchmark realistic batch sizes;
the initial implementation materializes affected rows and snapshots.

PostgreSQL serializes concurrent history writes with row locks. Normal database
errors, deadlocks, or connection failures are propagated and require the application's
usual transaction retry policy. SQLite does not provide equivalent row-lock semantics;
concurrent writers can receive a database-locked error. No silent retry or partial
success is claimed for a single supported mutation.

The sync SDK uses sync ORM calls. Use Django's async model/queryset methods in async
handlers. Call synchronous history/backfill/restore functions through
`asgiref.sync.sync_to_async(..., thread_sensitive=True)` when needed.
