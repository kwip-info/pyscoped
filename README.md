# PyScoped

**Tenant scoping and transactional audit history for the Django application you already have.**

Keep your models, tables, primary keys, authentication, and business logic. Add PyScoped
one model at a time. Free and MIT licensed, with no account, API key, paid tier,
telemetry, or hosted-service dependency.

**2.0.0 is the stable Django release.** It is a
breaking redesign of 1.x. The new import is `pyscoped`; the old `scoped` framework is
preserved in `legacy/v1/` and excluded from the package.

## Add it to an existing model

Install the stable release:

```sh
python -m pip install pyscoped==2.0.0
```

Add `"pyscoped"` to `INSTALLED_APPS`, then run `python manage.py migrate`.
Only PyScoped's history tables are added; your model needs no extra columns.

```python
from django.db import models
from pyscoped import scoped
from pyscoped.query import ScopedManager

@scoped(scope_field="organization", fields=["amount", "status"])
class Invoice(models.Model):
    organization = models.ForeignKey("Organization", on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=32, default="draft")

    objects = ScopedManager()
```

Use your existing authenticated user and organization after checking membership:

```python
from pyscoped import scope

with scope(actor=request.user, scope=organization.pk):
    invoices = list(Invoice.objects.filter(status="draft"))
    Invoice.objects.filter(pk=invoice_id).update(status="approved")
```

Reads through the scoped manager are filtered **before** aggregation or pagination.
Supported writes and audit records commit or roll back together. Missing context or
an instance write to another scope raises. An explicit field allowlist controls what
is recorded; it is your responsibility to exclude sensitive fields.

`scope()` is an application-level boundary: your application must authenticate the
actor and authorize scope membership. It does not grant a user permission merely
because a tenant ID was supplied by a client.

## Adopt gradually

- Begin with `mode="audit"` to retain existing read behavior while adding attributed
  write history. Writes still require actor context and the supported manager.
- Use `@scoped(...)` on a current model, or `register(Invoice, ...)` in `AppConfig.ready()`.
- If convenient, inherit `pyscoped.models.ScopedModel`; it adds a manager, no columns.
  The registration decorator is still required.
- Compose existing queryset methods with `ScopedQuerySet`. Supply `ScopedManager`
  for **every** public manager on a registered model.
- Baseline existing data, then enable enforcement for that model when its workflows
  and relationship integrity have been checked.

```sh
# Preview first; actor and scope are explicit operator assertions.
python manage.py pyscoped_backfill billing.Invoice --actor migration-operator --scope acme
python manage.py pyscoped_backfill billing.Invoice --actor migration-operator --scope acme --apply
```

The baseline preserves existing IDs and records the state observed now. It does not
invent historical events. Applying is idempotent and resumable per row.

For request context, add `pyscoped.middleware.ScopedMiddleware` after your existing
session/authentication middleware and configure `PYSCOPED_CONTEXT_RESOLVER` with a
trusted callable returning `ScopeContext` or `None`. For existing service functions,
use `@in_scope(resolver)` from `pyscoped.integration`.

## History and restoration

```python
from pyscoped.history import history, verify_history, restore

with scope(actor=request.user, scope=organization.pk):
    events = history(Invoice, invoice_id)
    verified = verify_history(Invoice, invoice_id)
    invoice = restore(Invoice, invoice_id, revision=1, expected_revision=3)
```

Restoration updates selected scalar fields on a live row and records a **new** event.
It checks the expected head revision and detects divergence from recorded state.
It does not resurrect deleted rows, restore external effects, or change ownership,
primary keys, or relationships. History is hash-linked per resource; rewriting the
entire chain and head with database-administrator privileges is outside that guarantee.

## Supported scope

Django 5.2 and 6.0, on their supported Python versions. SQLite for local development;
PostgreSQL for concurrent transactional use. There are no non-Django adapters in 2.0.

| Operation | Initial 2.0 behavior |
| --- | --- |
| Model save, partial save, expression save | Attributed and audited atomically |
| Scoped query reads, counts, aggregates, pagination | Scope predicate enforced at the root queryset |
| Queryset update | Native SQL semantics, row locks and per-row audit snapshots in one transaction |
| Instance/queryset/cascade delete | Django deletion semantics; retained audit events |
| Async ORM operations | Same contract via Django's async methods |
| Ordinary `bulk_create`, `bulk_update` | Native Django signal semantics, transactional audit snapshots |
| Conflict-handling bulk insert, duplicate bulk-update IDs, raw fixture save | Explicitly rejected |
| `select_related`, joins into enforced models | Scope predicates on joined tables; outer-join semantics preserved |
| FK / one-to-one reads between enforced models, prefetch | Scoped target queries and context checks |
| Implicit many-to-many, multi-table inheritance, proxies | Outside initial support; configuration fails |
| Raw SQL, `RawSQL`, private ORM APIs, historical migration models | Outside enforcement; never use for ordinary registered operations |

This is an ORM integration, not database row-level security or a sandbox against
untrusted Python code. Unregistered models, external database writers, direct private/base-manager access,
and already-returned data do not acquire automatic access controls. Preserve valid
same-scope relationships and use scoped querysets for each protected read boundary.

See [the full guarantees and limitations](docs/guarantees.md),
[the adoption guide](docs/adoption.md), and the
[executable existing-app example](examples/existing_app/README.md).

## Develop and contribute

```sh
python -m pip install -e '.[dev,postgres]'
python -m pytest -q
python -m django check --settings=tests.settings
python -m django makemigrations --check --dry-run --settings=tests.settings
ruff check pyscoped tests examples
python -m build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for PostgreSQL tests,
[the phased development plan](docs/development/PLAN.md) for scope and progress,
and [the release record](docs/development/RELEASE_READINESS.md) for validation evidence.
