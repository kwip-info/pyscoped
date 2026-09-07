---
title: Multi-Tenant Isolation
description: Guide to pyscoped's three isolation tiers — Postgres RLS, database-per-tenant routing, and combining both for maximum security.
category: guides
---

# Multi-Tenant Isolation

pyscoped provides three tiers of tenant isolation, each operating
independently. You can use any tier alone or combine them for defense-in-depth.

---

## Overview

| Tier | Mechanism | Isolation boundary | When to use |
|------|-----------|--------------------|-------------|
| **Application** | `ScopedManager` owner checks | Process memory | Always on (default) |
| **Tier 1: Postgres RLS** | Row-level security policies | Database engine | Multi-tenant on a shared database |
| **Tier 2: Database-per-tenant** | `TenantRouter` | Separate databases | Regulatory, contractual, or compliance requirements |

The application-layer tier (Layer 3) is always active. Tiers 1 and 2 are
opt-in and add progressively stronger isolation guarantees.

**Recommendation:** For most SaaS applications, Tier 1 (RLS) is sufficient.
Use Tier 2 when tenants require contractual guarantees of physical data
separation, when regulatory requirements mandate it, or when tenants have
wildly different performance profiles that could cause noisy-neighbor issues.

---

## Tier 1: Postgres Row-Level Security

RLS makes the database itself enforce isolation. Even if application code has a
bug that bypasses `ScopedManager`, the database rejects unauthorized access.

### Setup

**Step 1:** Enable RLS on the backend.

```python
from scoped.storage.sa_postgres import SASAPostgresBackend

backend = SAPostgresBackend(
    "postgresql://user:pass@localhost:5432/myapp",
    enable_rls=True,
)
backend.initialize()
```

**Step 2:** Run migration m0013 to create RLS policies.

```python
from scoped.storage.migrations.runner import MigrationRunner

runner = MigrationRunner(backend)
runner.discover()
runner.apply_all()
```

Migration m0013 creates policies on all tables with `owner_id` columns, plus
specialized policies for memberships, projections, and notifications.

### How SET/RESET works per connection

When `enable_rls=True`, the `SAPostgresBackend` injects the current principal
ID into every database connection before executing queries:

```
┌─────────────────────────────────────────────────┐
│  Application Code                                │
│  with scoped.as_principal(alice):                │
│      scoped.objects.list()                       │
│                                                   │
│  ┌─────────────────────────────────────────────┐ │
│  │  SAPostgresBackend                             │ │
│  │  1. Get connection from pool                 │ │
│  │  2. SET app.current_principal_id = 'alice'   │ │
│  │  3. Execute: SELECT * FROM scoped_objects    │ │
│  │     → RLS filter: WHERE owner_id = 'alice'   │ │
│  │  4. RESET app.current_principal_id           │ │
│  │  5. Return connection to pool                │ │
│  └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

### SET LOCAL for transactions vs SET for autocommit

The backend uses two approaches depending on the context:

**Explicit transactions** use `SET LOCAL`:

```python
# SAPostgresBackend.transaction()
txn = backend.transaction()
# Internally: SET LOCAL app.current_principal_id = 'alice'
# SET LOCAL is scoped to the transaction — automatically reset on COMMIT/ROLLBACK
```

`SET LOCAL` is the right choice for transactions because:
- It is automatically reset when the transaction ends.
- No explicit `RESET` is needed.
- If the transaction rolls back, the setting rolls back too.

**Autocommit operations** use session-level `SET`:

```python
# SAPostgresBackend.execute(), fetch_one(), fetch_all()
# Internally:
#   SET app.current_principal_id = 'alice'   (session-level)
#   ... execute query ...
#   RESET app.current_principal_id           (explicit cleanup)
```

Session-level `SET` is necessary for autocommit because `SET LOCAL` is a no-op
outside a transaction block. The explicit `RESET` prevents leaking the
principal ID to the next query on the same pooled connection.

### FORCE ROW LEVEL SECURITY

Standard RLS policies do not apply to the Postgres role that owns the table.
This is a Postgres security feature — table owners are exempt from their own
policies by default. In production, the application typically connects as the
table owner, which would make RLS useless.

Migration m0013 applies `FORCE ROW LEVEL SECURITY` on every protected table:

```sql
ALTER TABLE scoped_objects FORCE ROW LEVEL SECURITY;
```

This ensures policies apply even when connected as the table owner.

### Protected tables

RLS policies are applied to:

**Owner-based policies** (`owner_id` column):
`scoped_objects`, `scopes`, `secrets`, `secret_versions`, `environments`,
`environment_templates`, `stages`, `pipelines`, `deployment_targets`,
`contracts`, `blobs`, `search_index`, `templates`, `retention_policies`,
`glacial_archives`, `event_subscriptions`, `webhook_endpoints`,
`notification_rules`, `recurring_schedules`, `scheduled_actions`, `jobs`.

**Membership policy** (`principal_id` column):
`scope_memberships` — a principal can only see their own memberships.

**Projection policy** (subquery on membership):
`scope_projections` — visible if the principal is an active member of the
projection's scope.

**Notification policy** (`recipient_id` column):
`notifications` — a principal can only see notifications addressed to them.

### Safe deny-all default

When no `ScopedContext` is active, `_get_rls_principal_id()` returns an empty
string. The RLS policy evaluates to:

```sql
WHERE owner_id = ''
```

This matches no rows, effectively denying all access. This is the safe
default — if context injection fails for any reason, the database returns zero
rows rather than all rows.

---

## Tier 2: Database-Per-Tenant

For the strongest isolation guarantee, `TenantRouter` routes each tenant to
their own dedicated database. Tenant data never shares tables, connections, or
storage.

### Constructor

```python
from scoped.storage.tenant_router import TenantRouter
from scoped.storage.sa_postgres import SASAPostgresBackend

router = TenantRouter(
    tenant_resolver=my_tenant_resolver,
    backend_factory=my_backend_factory,
    default_tenant_id="system",  # Optional fallback
)
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `tenant_resolver` | `(principal_id: str) -> str` | Maps a principal ID to a tenant ID. Called on every operation using the active `ScopedContext`. |
| `backend_factory` | `(tenant_id: str) -> StorageBackend` | Creates a new storage backend for a tenant. Should NOT call `initialize()` — the router handles that. |
| `default_tenant_id` | `str \| None` | Fallback tenant ID when no `ScopedContext` is active. If `None`, operations without context raise `TenantResolutionError`. |

### Tenant resolver

The resolver maps principal IDs to tenant IDs. This is application-specific —
you might look it up in a central directory, parse it from the principal ID, or
query a tenant-mapping table:

```python
# Example: lookup in a central directory
def resolve_tenant(principal_id: str) -> str:
    """Map principal to tenant via a central directory service."""
    mapping = {
        "alice-uuid": "acme-corp",
        "bob-uuid": "globex-inc",
    }
    tenant_id = mapping.get(principal_id)
    if tenant_id is None:
        raise TenantResolutionError(f"Unknown principal: {principal_id}")
    return tenant_id
```

### Backend factory

The factory creates a new storage backend for each tenant. It is called once
per tenant (on first access) and the result is cached:

```python
def make_backend(tenant_id: str) -> SAPostgresBackend:
    """Create a dedicated Postgres database connection for a tenant."""
    return SAPostgresBackend(
        f"postgresql://app:password@db-host/{tenant_id}_db",
        pool_size=5,
    )
```

### Provisioning a tenant

```python
router = TenantRouter(
    tenant_resolver=resolve_tenant,
    backend_factory=make_backend,
)
router.initialize()

# Provision a new tenant — creates backend, initializes full schema
backend = router.provision_tenant("acme-corp")
```

`provision_tenant()` is idempotent. If the tenant already exists, it returns
the existing backend. Internally, it calls `backend_factory(tenant_id)` and
then `backend.initialize()`, which creates the complete pyscoped schema in the
tenant's database.

### Listing and tearing down tenants

```python
# List all provisioned tenants
tenants = router.list_tenants()
print(tenants)  # ["acme-corp", "globex-inc"]

# Get a specific tenant's backend (without auto-provisioning)
backend = router.get_tenant_backend("acme-corp")

# Tear down a tenant — closes connection pool, removes from cache
# Does NOT drop the database
router.teardown_tenant("acme-corp")
```

### How routing works from ScopedContext

When any storage operation is called on the router, it:

1. Reads the active `ScopedContext` to get the principal ID.
2. Calls `tenant_resolver(principal_id)` to get the tenant ID.
3. Looks up or creates the backend for that tenant.
4. Delegates the operation to the tenant's backend.

```python
with scoped.as_principal(alice):
    # Router resolves: alice.id -> "acme-corp" -> acme's SAPostgresBackend
    scoped.objects.create("invoice", data={"amount": 500})
    # This INSERT goes to the acme-corp database

with scoped.as_principal(bob):
    # Router resolves: bob.id -> "globex-inc" -> globex's SAPostgresBackend
    scoped.objects.create("invoice", data={"amount": 300})
    # This INSERT goes to the globex-inc database
```

### Thread-safe backend cache

The `TenantRouter` maintains a dictionary of initialized backends keyed by
tenant ID. Access to this cache uses double-checked locking:

1. **Fast path** (no lock): Check if the backend exists in the dict. If yes,
   return it immediately.
2. **Slow path** (with lock): Acquire `threading.Lock`, check again (another
   thread may have created it), then create and initialize the backend.

This pattern ensures that only one thread creates a backend for any given
tenant, while all subsequent requests take the lock-free fast path.

---

## Combining Tier 1 + Tier 2

For maximum isolation, use `TenantRouter` with RLS-enabled backends. Each
tenant gets their own database AND row-level security within that database:

```python
from scoped.storage.tenant_router import TenantRouter
from scoped.storage.sa_postgres import SASAPostgresBackend
from scoped.storage.migrations.runner import MigrationRunner

def make_rls_backend(tenant_id: str) -> SAPostgresBackend:
    """Create an RLS-enabled backend for a tenant."""
    backend = SAPostgresBackend(
        f"postgresql://app:password@db-host/{tenant_id}_db",
        enable_rls=True,
        pool_size=5,
    )
    return backend  # Do NOT call initialize() — the router does it

router = TenantRouter(
    tenant_resolver=resolve_tenant,
    backend_factory=make_rls_backend,
)
router.initialize()

# After provisioning, apply migrations for each tenant
for tenant_id in ["acme-corp", "globex-inc"]:
    backend = router.provision_tenant(tenant_id)
    runner = MigrationRunner(backend)
    runner.discover()
    runner.apply_all()  # Includes m0013 (RLS policies)
```

With this configuration:
- **Database boundary** prevents any cross-tenant query at the Postgres level.
- **RLS** prevents any cross-principal query within a tenant's database.
- **Application layer** (`ScopedManager`) provides the first line of defense.

---

## Working with ScopedClient

Pass the `TenantRouter` as the `backend` parameter to `ScopedClient`:

```python
import scoped
from scoped.storage.tenant_router import TenantRouter
from scoped.storage.sa_postgres import SASAPostgresBackend

router = TenantRouter(
    tenant_resolver=resolve_tenant,
    backend_factory=lambda tid: SAPostgresBackend(
        f"postgresql://app:pass@host/{tid}_db"
    ),
)
router.initialize()

# Use the router as the backend
client = scoped.ScopedClient(backend=router)

# All namespace operations now route through the tenant router
alice = client.principals.create("Alice")
with client.as_principal(alice):
    doc, v1 = client.objects.create("report", data={"q": "Q4"})
```

You can also use the module-level API by passing the router to `init()`:

```python
# Note: init() expects database_url, so use the backend parameter directly
client = scoped.ScopedClient(backend=router)
```

---

## Limitations

### Cross-tenant JOINs not possible in Tier 2

When using database-per-tenant, each tenant's data lives in a separate
PostgreSQL database. SQL JOINs across databases are not supported. If you need
to aggregate data across tenants (e.g. for a global dashboard), you must query
each tenant's backend separately:

```python
all_counts = {}
for tenant_id in router.list_tenants():
    backend = router.get_tenant_backend(tenant_id)
    if backend is not None:
        row = backend.fetch_one("SELECT COUNT(*) as cnt FROM scoped_objects")
        all_counts[tenant_id] = row["cnt"]
```

### Scope projections do not work across databases

Scope projections (sharing) operate within a single database. If Alice is in
tenant A and Bob is in tenant B, Alice cannot project her objects into a scope
that Bob is a member of — they are in separate databases with separate scope
tables.

For cross-tenant collaboration, use the Connector layer (Layer 13), which
provides governed federation between separate pyscoped instances.

### Tenant teardown does not drop the database

`router.teardown_tenant(tenant_id)` closes the connection pool and removes the
backend from the cache, but it does not drop the database. Database cleanup is
the caller's responsibility:

```python
router.teardown_tenant("acme-corp")
# The acme-corp database still exists — drop it manually if needed:
# DROP DATABASE acme_corp_db;
```

---

## Migration Orchestration

When using `TenantRouter`, each tenant's backend runs `initialize()` which
creates the full pyscoped schema. For migrations beyond the initial schema, you
must apply them to each tenant independently:

```python
from scoped.storage.migrations.runner import MigrationRunner

def apply_migrations_to_all_tenants(router: TenantRouter) -> dict[str, str]:
    """Apply pending migrations to every provisioned tenant."""
    results = {}
    for tenant_id in router.list_tenants():
        backend = router.get_tenant_backend(tenant_id)
        if backend is None:
            results[tenant_id] = "not found"
            continue
        runner = MigrationRunner(backend)
        runner.discover()
        status = runner.get_status()
        pending = [m for m in status if not m["applied"]]
        if pending:
            runner.apply_all()
            results[tenant_id] = f"applied {len(pending)} migrations"
        else:
            results[tenant_id] = "up to date"
    return results
```

### Migration order

Migrations are numbered m0001 through m0013 and must be applied in order:

| Migration | Purpose |
|-----------|---------|
| m0001 | Initial schema (all core tables) |
| m0002 | Contracts and validation |
| m0003 | Blob / media storage |
| m0004 | Scope settings |
| m0005 | Search index |
| m0006 | Templates |
| m0007 | Storage tiering |
| m0008 | Events and webhooks |
| m0009 | Notifications |
| m0010 | Scheduling |
| m0011 | Sync state |
| m0012 | Composite indexes |
| m0013 | Row-level security |

### Rolling back migrations

Migrations support rollback in reverse order:

```python
runner = MigrationRunner(backend)
runner.discover()
runner.rollback_last()  # Rolls back the most recently applied migration
```

For RLS specifically, rolling back m0013 drops all RLS policies and disables
RLS on every protected table, restoring the database to application-layer-only
isolation.

---

## Architecture Decision: Why Three Tiers?

**The application layer is fast but fallible.** Python code can have bugs.
`ScopedManager` can be bypassed by accessing the storage backend directly
(during debugging, in migrations, or through code errors). Application-layer
isolation is necessary but not sufficient.

**Postgres RLS is robust but limited.** RLS catches what the application layer
misses, but it operates within a single database. It cannot prevent a
connection from one tenant's pool from accidentally connecting to another
tenant's database (a misconfiguration issue, not a Postgres issue).

**Database-per-tenant is definitive but expensive.** Separate databases provide
absolute isolation — there is no possible cross-contamination. But they
require more infrastructure (more connection pools, more backups, more
monitoring) and make cross-tenant operations impossible.

The three tiers are designed to be layered. Use Tier 1 for defense-in-depth on
a shared database. Add Tier 2 when the business requires physical separation.
The application layer is always active regardless of which database tiers you
enable.
