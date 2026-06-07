# AGENTS.md — pyscoped

> Agent-oriented guide for working in projects that use **pyscoped**. Vendor-neutral
> companion to `CLAUDE.md`. If you are an AI coding agent, read this before generating
> or modifying code that touches the framework. The full reference lives in `CLAUDE.md`.

## What is pyscoped?

pyscoped is a Python framework for building enterprise-grade, compliant software with
built-in data **isolation**, **versioning**, **audit trails**, and **access control**.
Every object is creator-private by default; sharing is explicit via scope projections;
every mutation is versioned; every action is recorded in a tamper-evident, hash-chained
audit trail.

- **Install:** `pip install pyscoped` (extras below) · **Python:** 3.11+
- **Storage:** SQLite (dev), PostgreSQL (prod)
- **Import name:** the package is `pyscoped`; the import is `import scoped`.

### Install extras

```bash
pip install pyscoped              # core SDK
pip install "pyscoped[django]"    # Django + DRF integration  (this guide)
pip install "pyscoped[fastapi]"   # FastAPI middleware
pip install "pyscoped[flask]"     # Flask extension
pip install "pyscoped[mcp]"       # Model Context Protocol server
```

## Rules for agents (read first)

1. **Never bypass the SDK to mutate state.** Use `scoped.objects`, `scoped.scopes`,
   etc. Do not write directly to pyscoped's tables — you will break the audit hash chain.
2. **Every operation needs a principal.** Wrap work in `with scoped.as_principal(p):`
   or rely on the active `ScopedContext` (the framework integrations set this per-request).
   Operating with no principal raises.
3. **Don't pass actor IDs explicitly when a context is active.** `owner_id`,
   `principal_id`, `granted_by`, etc. are inferred from the active `ScopedContext`.
4. **Nothing is shared by default.** If another principal needs to see an object, add
   them to a scope and `project()` the object into it. Don't reach for raw queries.
5. **Never hard-delete.** `delete()` is a soft-delete (tombstone). There is no real DELETE.
6. **Secrets go through `scoped.secrets`,** never plain object data. Secret values are
   encrypted at rest and excluded from the audit state.
7. **Treat versions as immutable.** `update()` creates a new `ObjectVersion`; it never
   edits in place.

## 10 Core Invariants

1. Nothing exists without registration — every construct gets a URN.
2. Nothing happens without identity — every operation needs a principal.
3. Nothing is shared by default — creator-private until projected into a scope.
4. Nothing happens without trace — every mutation is audited.
5. Nothing is truly deleted — soft-delete only (tombstones).
6. Deny always wins — DENY rules override ALLOW rules.
7. Revocation is immediate — no grace period, same-transaction.
8. Everything is versioned — mutations create new immutable versions.
9. Everything is rollbackable — point-in-time reconstruction.
10. Secrets never leak — encrypted at rest, excluded from audit state.

## Quick Start

```python
import scoped

client = scoped.init()                       # in-memory SQLite, zero config

alice = scoped.principals.create("Alice")
bob = scoped.principals.create("Bob")

with scoped.as_principal(alice):
    doc, v1 = scoped.objects.create("invoice", data={"amount": 500})   # creator-private
    doc, v2 = scoped.objects.update(doc.id, data={"amount": 600})      # new version

    team = scoped.scopes.create("Engineering")
    scoped.scopes.add_member(team, bob, role="editor")
    scoped.scopes.project(doc, team)         # Bob can now see doc

    assert scoped.audit.verify().valid       # hash chain intact
```

PostgreSQL with optional management-plane sync:

```python
client = scoped.init(
    database_url="postgresql://user:pass@host/db",
    api_key="psc_live_...",   # optional: syncs usage to the management plane
)
client.start_sync()
```

## Core API (cheat sheet)

Actor params are inferred from the active context when omitted; object params also accept string IDs.

```python
# Identities
scoped.principals.create(display_name, *, kind="user", metadata=None) -> Principal
scoped.principals.get(pid) / .find(pid) / .list(*, kind=None)

# Objects (versioned, isolation-enforced)
scoped.objects.create(object_type, *, data, change_reason="created") -> (ScopedObject, ObjectVersion)
scoped.objects.get(object_id) -> ScopedObject | None
scoped.objects.update(object_id, *, data, change_reason="") -> (ScopedObject, ObjectVersion)
scoped.objects.delete(object_id, *, reason="") -> Tombstone        # soft-delete
scoped.objects.list(*, object_type=None, order_by="-created_at", limit=100)
scoped.objects.versions(object_id) -> list[ObjectVersion]

# Scopes (sharing)
scoped.scopes.create(name, *, parent_scope_id=None) -> Scope
scoped.scopes.add_member(scope, principal, *, role="viewer")       # viewer|editor|admin|owner
scoped.scopes.project(obj, scope, *, access_level="read")          # read|write|admin — share
scoped.scopes.unproject(obj, scope)                               # revoke sharing

# Audit (hash-chained, immutable)
scoped.audit.for_object(object_id) / .for_principal(pid) / .query(...)
scoped.audit.export(*, format="json"|"csv", **query)
scoped.audit.verify() -> ChainVerification                        # .valid

# Secrets (encrypted vault)
scoped.secrets.create(name, value, *, classification="standard") -> (Secret, SecretVersion)
scoped.secrets.resolve(ref_token, *, accessor_id=None) -> str
```

`data` accepts a `dict` or a registered typed instance (Pydantic model, dataclass, or
`ScopedSerializable`); `ObjectVersion.typed_data` deserializes back.

---

## Integration: `pyscoped[django]`

> How the Django adapter works and how to use it in a project. This is the integration
> the pyscoped management plane itself runs (it dogfoods the framework).

### What `pyscoped[django]` gives you

Installing `pip install "pyscoped[django]"` pulls in Django (`>=4.2`) and unlocks the
`scoped.contrib.django` package, which provides:

| Component | Import path | Purpose |
|-----------|-------------|---------|
| App config | `scoped.contrib.django` (add to `INSTALLED_APPS`) | Initializes the `DjangoORMBackend` on `ready()` so `scoped.*` works in every view |
| Middleware | `scoped.contrib.django.middleware.ScopedContextMiddleware` | Resolves the acting principal per request and wraps the handler in a `ScopedContext` (sync **and** async, Django 4.1+) |
| Base model | `scoped.contrib.django.models.ScopedModel` | Abstract model that auto-syncs Django rows with pyscoped's object/version/audit layers |
| Manager/QuerySet | `ScopedDjangoManager`, `ScopedQuerySet` | `.for_principal(pid)` filters by pyscoped visibility |
| DRF | `scoped.contrib.django.rest_framework.*` | Authentication + permission classes |
| Helper | `scoped.contrib.django.models.scoped_context_for` | Context manager for management commands / Celery |
| Backend | `scoped.contrib.django.backend.DjangoORMBackend` | Stores pyscoped's schema in your existing Django Postgres DB |

### 1. Wire it into `settings.py`

```python
INSTALLED_APPS = [
    # ...
    "scoped.contrib.django",   # initializes the backend on app ready()
]

MIDDLEWARE = [
    # ... after AuthenticationMiddleware ...
    "scoped.contrib.django.middleware.ScopedContextMiddleware",
]

# Tell the middleware how to resolve the acting principal from a request.
SCOPED_PRINCIPAL_RESOLVER = "myapp.resolvers.resolve_principal"

# Skip principal resolution on these path prefixes (health checks, docs, webhooks…).
SCOPED_EXEMPT_PATHS = ["/admin/", "/health/", "/docs", "/webhooks/"]

# Optional:
# SCOPED_BACKEND_USING    = "default"                       # Django DB alias
# SCOPED_API_KEY          = os.environ["SCOPED_API_KEY"]    # management-plane sync
# SCOPED_PRINCIPAL_HEADER = "HTTP_X_SCOPED_PRINCIPAL_ID"    # fallback header resolver
```

pyscoped's tables live **alongside** your own models in the same Postgres database, so a
single `python manage.py migrate` covers both. No second datastore to run.

### 2. Resolve the principal

The middleware calls `SCOPED_PRINCIPAL_RESOLVER` with the request and expects a
`Principal` (or `None` to run the request without a context):

```python
# myapp/resolvers.py
import scoped

def resolve_principal(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return None
    # Map your auth identity to a pyscoped principal (find-or-create).
    return scoped.principals.find(str(user.id)) or scoped.principals.create(
        user.get_username(), principal_id=str(user.id)
    )
```

The resolver may be sync or async — the middleware awaits awaitables automatically.
If no resolver is set, the middleware falls back to the `SCOPED_PRINCIPAL_HEADER`
(default `X-Scoped-Principal-Id`).

### 3. Use the SDK in views

Once the middleware has set the context, every view operates as the resolved principal —
no actor IDs to thread through:

```python
# myapp/views.py
import scoped

def create_invoice(request):
    invoice, version = scoped.objects.create("invoice", data={"amount": 500})
    return JsonResponse({"id": invoice.id, "version": version.version})

def my_invoices(request):
    # Only objects this principal can see (creator-private + projected scopes).
    return JsonResponse({"invoices": [o.id for o in scoped.objects.list(object_type="invoice")]})
```

### 4. (Optional) `ScopedModel` — keep Django models in sync

Use `ScopedModel` when you want a normal Django model whose lifecycle is mirrored into
pyscoped's object/version/audit layers automatically:

```python
from django.db import models
from scoped.contrib.django.models import ScopedModel

class Invoice(ScopedModel):
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)

    class ScopedMeta:
        object_type = "invoice"          # REQUIRED — subclasses must set this
        scoped_fields = ["amount", "currency"]   # None = sync all fields
```

- `save()` atomically persists to Django **and** creates/updates the matching `ScopedObject` + version.
- `delete()` atomically tombstones the pyscoped object **and** deletes the Django row.
- `Invoice.scoped_objects.for_principal(pid)` returns only rows visible to that principal.
- `scoped_fields` controls which columns are synced into pyscoped versions (`None` = all).

### 5. Django REST Framework

```python
# settings.py
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "scoped.contrib.django.rest_framework.ScopedAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "scoped.contrib.django.rest_framework.IsScopedPrincipal",
    ],
}
```

Available classes: `ScopedAuthentication`, `IsScopedPrincipal`, `HasScopeAccess`, `ScopedUser`.

### 6. Outside the request cycle (commands, Celery, scripts)

There is no request to resolve a principal from, so set the context explicitly:

```python
from scoped.contrib.django.models import scoped_context_for

with scoped_context_for(principal_id):
    Invoice.objects.create(amount=100, currency="USD")   # audited as that principal
```

### Common pitfalls

- **`object_type` empty** → `ScopedModel.save()` raises. Always set `ScopedMeta.object_type`.
- **No principal in context** → operations raise. Either the path is missing from
  `SCOPED_EXEMPT_PATHS` and the resolver returned `None`, or you're outside a request and
  forgot `scoped_context_for(...)`.
- **Middleware order** → place `ScopedContextMiddleware` *after* the auth middleware your
  resolver depends on.
- **Empty `display_name`** and other invariant violations raise immediately (fail-fast).

---

## Other integrations (brief)

```python
# FastAPI
from scoped.contrib.fastapi.middleware import ScopedContextMiddleware
app.add_middleware(ScopedContextMiddleware, database_url="postgresql://...")
# Principal from x-scoped-principal-id header; HTTP + WebSocket supported.

# Flask
from scoped.contrib.flask.extension import Scoped
Scoped(app)   # or scoped_ext.init_app(app); config: SCOPED_DATABASE_URL, SCOPED_API_KEY

# MCP (Model Context Protocol)
from scoped.contrib.mcp import create_scoped_server
server = create_scoped_server(client)
# Tools: create_principal, create_object, get_object, create_scope, list_audit, health_check
```

## Exceptions to expect

`PrincipalNotFoundError`, `ObjectNotFoundError`, `AccessDeniedError`,
`ScopeFrozenError`, `SecretLeakageError`, plus `ValueError` for invariant violations
(empty names, invalid URNs, version < 1). Handle `AccessDeniedError` as a 403 in your
own app; don't swallow it.

## More

- Full reference: `CLAUDE.md` (download from `/docs/claude.md` on the platform).
- Docs index: `/docs/manifest.json` (SDK) and `/docs/platform/manifest.json` (platform).
- Raw markdown: `/docs/raw/<path>`.
