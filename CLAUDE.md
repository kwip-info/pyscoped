# pyscoped — Universal Object-Isolation and Tenancy-Scoping Framework

## What is pyscoped?

pyscoped is a Python framework for building enterprise-grade, compliant software with built-in data isolation, versioning, audit trails, and access control. Every object is creator-private by default. Sharing is explicit via scope projections. Every mutation is versioned. Every action is recorded in a tamper-evident hash-chained audit trail.

**Package:** `pip install pyscoped` | **Python:** 3.11+ | **Storage:** SQLite (dev), PostgreSQL (prod)

## Architecture — 16 Composable Layers

Each layer depends only on layers below it. Layer 0 validates invariants across all.

| Layer | Name | Purpose |
|-------|------|---------|
| 0 | Compliance | Invariant validation across all layers |
| 1 | Registry | Universal construct registration (URNs) |
| 2 | Identity | Principals (users, teams, services) + ScopedContext |
| 3 | Objects | Versioned, isolation-enforced data objects |
| 4 | Tenancy | Scopes, membership, projections (sharing) |
| 5 | Rules | Policy engine (DENY overrides ALLOW) |
| 6 | Audit | Hash-chained immutable trail (SHA-256) |
| 7 | Temporal | Rollback, point-in-time reconstruction |
| 8 | Environments | Ephemeral workspaces |
| 9 | Flow | Stages, pipelines, promotions |
| 10 | Deployments | Graduation to external targets |
| 11 | Secrets | Encrypted vault with zero-trust access |
| 12 | Integrations | Sandboxed plugins |
| 13 | Connector | Cross-org federation, marketplace |
| 14 | Events | Async event bus + webhooks |
| 15 | Notifications | Principal-targeted messages |
| 16 | Scheduling | Recurring schedules, scoped job execution |

## 10 Core Invariants

1. **Nothing exists without registration** — every construct gets a URN
2. **Nothing happens without identity** — every operation needs a principal
3. **Nothing is shared by default** — creator-private until projected into a scope
4. **Nothing happens without trace** — every mutation is audited
5. **Nothing is truly deleted** — soft-delete only (tombstones)
6. **Deny always wins** — DENY rules override ALLOW rules
7. **Revocation is immediate** — no grace period, same-transaction
8. **Everything is versioned** — mutations create new immutable versions
9. **Everything is rollbackable** — point-in-time reconstruction
10. **Secrets never leak** — encrypted at rest, excluded from audit state

## Quick Start

```python
import scoped

# Initialize (in-memory SQLite, zero config)
client = scoped.init()

# Create identities
alice = scoped.principals.create("Alice")
bob = scoped.principals.create("Bob")

# Set acting principal for a block
with scoped.as_principal(alice):
    # Create an object — creator-private by default
    doc, v1 = scoped.objects.create("invoice", data={"amount": 500})

    # Update creates a new immutable version
    doc, v2 = scoped.objects.update(doc.id, data={"amount": 600})

    # Create a scope and share
    team = scoped.scopes.create("Engineering")
    scoped.scopes.add_member(team, bob, role="editor")
    scoped.scopes.project(doc, team)  # Bob can now see doc

    # Verify audit chain integrity
    assert scoped.audit.verify().valid
```

### PostgreSQL with management plane sync
```python
client = scoped.init(
    database_url="postgresql://user:pass@host/db",
    api_key="psc_live_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
)
client.start_sync()
```

### Multi-client (testing, multiple databases)
```python
client_a = scoped.ScopedClient(database_url="sqlite:///a.db")
client_b = scoped.ScopedClient(database_url="sqlite:///b.db")
```

## Namespace API Reference

After `scoped.init()`, all namespaces are available at module level: `scoped.principals`, `scoped.objects`, `scoped.scopes`, `scoped.audit`, `scoped.secrets`. Or via client instance: `client.principals`, etc.

All actor parameters (`owner_id`, `principal_id`, `granted_by`, etc.) are **inferred from the active ScopedContext** when omitted. All parameters accepting objects also accept string IDs.

### scoped.principals

```python
create(display_name, *, kind="user", metadata=None, principal_id=None) -> Principal
get(principal_id) -> Principal                    # Raises PrincipalNotFoundError
find(principal_id) -> Principal | None
update(principal, *, display_name=None, metadata=None) -> Principal
list(*, kind=None) -> list[Principal]
```

### scoped.objects

```python
create(object_type, *, data, owner_id=None, change_reason="created") -> (ScopedObject, ObjectVersion)
create_many(items, *, owner_id=None) -> list[(ScopedObject, ObjectVersion)]  # Atomic batch
get(object_id, *, principal_id=None) -> ScopedObject | None
update(object_id, *, data, principal_id=None, change_reason="") -> (ScopedObject, ObjectVersion)
delete(object_id, *, principal_id=None, reason="") -> Tombstone
list(*, principal_id=None, object_type=None, order_by="created_at", limit=100, offset=0) -> list[ScopedObject]
versions(object_id, *, principal_id=None, limit=None, offset=0) -> list[ObjectVersion]
```

`order_by` supports `-` prefix for descending: `"-created_at"`, `"object_type"`.

### scoped.scopes

```python
create(name, *, owner_id=None, description="", parent_scope_id=None, metadata=None) -> Scope
get(scope_id) -> Scope | None
rename(scope, new_name, *, renamed_by=None) -> Scope
update(scope, *, description=None, metadata=None, updated_by=None) -> Scope
list(*, owner_id=None, parent_scope_id=None, order_by="created_at", limit=None, offset=0) -> list[Scope]
count(*, owner_id=None, parent_scope_id=None) -> int

# Membership
add_member(scope, principal, *, role="viewer", granted_by=None) -> ScopeMembership
add_members(scope, members, *, granted_by=None) -> list[ScopeMembership]   # Batch
remove_member(scope, principal, *, revoked_by=None) -> int
members(scope) -> list[ScopeMembership]

# Projection (sharing)
project(obj, scope, *, projected_by=None, access_level="read") -> ScopeProjection
unproject(obj, scope, *, revoked_by=None) -> bool
projections(scope) -> list[ScopeProjection]

# Lifecycle
freeze(scope, *, frozen_by=None) -> Scope       # No new members/projections
archive(scope, *, archived_by=None) -> Scope     # Soft-delete, revoke all
```

Roles: `"viewer"`, `"editor"`, `"admin"`, `"owner"`. Access levels: `"read"`, `"write"`, `"admin"`.

### scoped.audit

```python
for_object(object_id, *, limit=100) -> list[TraceEntry]
for_principal(principal_id, *, limit=100) -> list[TraceEntry]
for_scope(scope_id, *, limit=100) -> list[TraceEntry]
query(*, actor_id=None, action=None, target_type=None, target_id=None,
      scope_id=None, since=None, until=None, order_by="sequence",
      limit=100, offset=0) -> list[TraceEntry]
verify(*, from_sequence=1, to_sequence=None) -> ChainVerification
```

`order_by` supports: `"sequence"`, `"timestamp"`, `"-sequence"`, `"-timestamp"`.

### scoped.secrets

```python
create(name, value, *, owner_id=None, description="", classification="standard") -> (Secret, SecretVersion)
rotate(secret_id, *, new_value, rotated_by=None, reason="rotation") -> SecretVersion
grant_ref(secret_id, principal, *, granted_by=None) -> SecretRef
resolve(ref_token, *, accessor_id=None) -> str   # Decrypted plaintext
```

## Data Models

### Principal
```python
id: str, kind: str, display_name: str, created_at: datetime,
created_by: str, lifecycle: Lifecycle, metadata: Metadata
```

### ScopedObject
```python
id: str, object_type: str, owner_id: str, current_version: int,
created_at: datetime, lifecycle: Lifecycle
```

### ObjectVersion
```python
id: str, object_id: str, version: int, data: dict,
created_at: datetime, created_by: str, change_reason: str, checksum: str
```

### Scope
```python
id: str, name: str, owner_id: str, description: str,
parent_scope_id: str | None, created_at: datetime,
lifecycle: Lifecycle, metadata: dict
```

### TraceEntry
```python
id: str, sequence: int, actor_id: str, action: ActionType,
target_type: str, target_id: str, timestamp: datetime,
hash: str, previous_hash: str, scope_id: str | None,
before_state: dict | None, after_state: dict | None, metadata: dict
```

### Lifecycle states
`DRAFT` -> `ACTIVE` -> `DEPRECATED` (frozen) -> `ARCHIVED`

## Isolation Model

### Application layer (Layer 3)
Every query routes through `ScopedManager` which enforces `owner_id == principal_id`. No bypass exists without going directly to the storage backend.

### Scope-based visibility (Layer 4)
Objects become visible to non-owners only through **scope projections + membership**:
1. Owner sees their objects (always)
2. Scope members see projected objects (explicit opt-in)
3. Child scope members inherit visibility from parent scopes
4. DENY rules (Layer 5) can further restrict

### Postgres Row-Level Security (opt-in)
Defense-in-depth. Enable with `PostgresBackend(dsn, enable_rls=True)` + run migration m0013.
- Sets `app.current_principal_id` per-connection from `ScopedContext`
- RLS policies: `USING (owner_id = current_setting('app.current_principal_id', true))`
- `FORCE ROW LEVEL SECURITY` ensures policies apply even for table owners
- Empty principal_id (no context) = deny all rows (safe default)

### Database-per-tenant (hard isolation)
```python
from scoped.storage.tenant_router import TenantRouter

router = TenantRouter(
    tenant_resolver=lambda principal_id: lookup_tenant(principal_id),
    backend_factory=lambda tenant_id: PostgresBackend(f"postgresql://host/{tenant_id}_db"),
)
router.initialize()

# With ScopedContext, all operations route to tenant's DB automatically
router.provision_tenant("tenant_123")  # Create + initialize schema
router.list_tenants()                  # ["tenant_123"]
router.teardown_tenant("tenant_123")   # Close + remove (doesn't drop DB)
```

## Rules Engine (Layer 5)

Deny-overrides model: ANY DENY = denied, at least one ALLOW + no DENY = allowed, no rules = allowed (when no rules are bound).

Rules are enforced in `ScopedManager` before `create()`, `update()`, and `tombstone()` operations. The `RuleEngine` is wired into the services container and injected into the manager automatically.

```python
# Create a DENY rule
rule = client.services.rules.create_rule(
    name="block-invoices", rule_type=RuleType.ACCESS,
    effect=RuleEffect.DENY,
    conditions={"action": "create", "object_type": "invoice"},
    priority=100, created_by="system",
)
client.services.rules.bind_rule(rule.id, target_type=BindingTargetType.OBJECT_TYPE,
                                 target_id="invoice", bound_by="system")

# Now this raises AccessDeniedError:
scoped.objects.create("invoice", data={...})
```

## Storage Backends

### SQLite (development / testing)
```python
scoped.init()                                    # In-memory
scoped.init(database_url="sqlite:///app.db")     # File
```

### PostgreSQL (production)
```python
scoped.init(database_url="postgresql://user:pass@host/db")
```
Features: connection pooling (psycopg v3 + psycopg_pool), tsvector full-text search, RLS support.

### StorageBackend interface
```python
class StorageBackend(ABC):
    dialect: str
    initialize() -> None
    transaction() -> StorageTransaction
    execute(sql, params) -> Any
    fetch_one(sql, params) -> dict | None
    fetch_all(sql, params) -> list[dict]
    close() -> None
    table_exists(table_name) -> bool
```

## Framework Integrations

### Django
```python
# settings.py
INSTALLED_APPS = ["scoped.contrib.django"]
MIDDLEWARE = ["scoped.contrib.django.middleware.ScopedContextMiddleware"]
SCOPED_PRINCIPAL_RESOLVER = "myapp.resolvers.resolve_principal"
SCOPED_EXEMPT_PATHS = ["/admin/", "/health/"]
```
Supports sync and async views (Django 4.1+).

### Django REST Framework
```python
# settings.py
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["scoped.contrib.django.rest_framework.ScopedAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["scoped.contrib.django.rest_framework.IsScopedPrincipal"],
}
```
Classes: `ScopedAuthentication`, `IsScopedPrincipal`, `HasScopeAccess`, `ScopedUser`.

### FastAPI
```python
from scoped.contrib.fastapi.middleware import ScopedContextMiddleware
app.add_middleware(ScopedContextMiddleware, database_url="postgresql://...")
```
Supports HTTP and WebSocket connections. Principal resolved from `x-scoped-principal-id` header.

### Flask
```python
from scoped.contrib.flask.extension import Scoped
Scoped(app)  # or scoped_ext.init_app(app) for app factory
# Config: SCOPED_DATABASE_URL, SCOPED_API_KEY
```

### MCP (Model Context Protocol)
```python
from scoped.contrib.mcp import create_scoped_server
server = create_scoped_server(client)
```
Exposes tools: create_principal, create_object, get_object, create_scope, list_audit, health_check.

## Events, Webhooks, and Notifications

### Event bus
```python
from scoped.events.bus import EventBus
bus = EventBus(backend, audit_writer=writer)
bus.emit(EventType.OBJECT_CREATED, actor_id="u1", target_type="Doc", target_id="d1")
bus.on(EventType.OBJECT_CREATED, my_listener)  # Type-specific listener
bus.on_any(my_global_listener)                  # Wildcard — receives ALL event types
```

### Webhook delivery
```python
from scoped.events.webhooks import WebhookDelivery
delivery = WebhookDelivery(backend, transport=WebhookDelivery.http_transport)
delivery.deliver_pending()                    # Attempt all pending
delivery.retry_failed(backoff_base=60)        # Exponential backoff (60s, 120s, 240s...)
```

### Notifications
```python
from scoped.notifications.engine import NotificationEngine
engine = NotificationEngine(backend, audit_writer=writer)
engine.process_event(event)   # Creates notifications from matching rules
engine.list_notifications(recipient_id="alice")
engine.mark_read(notification_id)
```

When using `ScopedServices`, the notification engine is automatically wired as a wildcard listener on the event bus. Accessing `services.notifications` triggers the wiring — any subsequent `services.events.emit()` call will automatically generate matching notifications.

## Scheduling and Jobs

```python
from scoped.scheduling.scheduler import Scheduler
from scoped.scheduling.queue import JobQueue

scheduler = Scheduler(backend, audit_writer=writer, cron_parser=my_cron_parser)
queue = JobQueue(backend, executor=my_executor)  # (action_type, config) -> result_dict

# Create schedule + action
schedule = scheduler.create_schedule(name="hourly", owner_id="system", interval_seconds=3600)
scheduler.create_action(name="cleanup", owner_id="system", action_type="run_cleanup",
                        next_run_at=now, schedule_id=schedule.id)

# Process all due actions → enqueue jobs → advance schedules
jobs = scheduler.process_due_actions(queue)
completed = queue.run_all()
```

## Connector Federation

```python
from scoped.connector.bridge import ConnectorManager
mgr = ConnectorManager(backend, transport=ConnectorManager.http_transport)

connector = mgr.propose(name="partner", local_org_id="us", remote_org_id="them",
                         remote_endpoint="https://partner.com/sync", created_by="admin")
mgr.submit_for_approval(connector.id, actor_id="admin")
mgr.approve(connector.id, actor_id="admin")

# Sync with policy enforcement + remote HTTP push
traffic = mgr.sync_object(connector.id, object_type="Doc", object_id="d1")
```

State machine: `PROPOSED -> PENDING_APPROVAL -> ACTIVE <-> SUSPENDED`, terminal: `REVOKED`, `REJECTED`.

## Structured Logging

```python
from scoped.logging import get_logger
logger = get_logger("myapp")
logger.audit("object.created", object_id="doc-1", object_type="invoice")
logger.info("Processing complete", count=5)
```
JSON output with timestamp, level, message, principal_id (from context), and custom fields. Configure via `SCOPED_LOG_LEVEL` env var.

## OpenTelemetry

```python
from scoped.contrib.otel import instrument
client = scoped.init(database_url="postgresql://...")
instrument(client)  # Wraps 21 operations with OTel spans
```
Covers: object CRUD, audit recording, secrets, scope lifecycle, principal management, rule evaluation.

## Migrations

```python
from scoped.storage.migrations.runner import MigrationRunner
runner = MigrationRunner(backend)
runner.discover()          # Auto-discover from scoped.storage.migrations.versions
runner.apply_all()         # Apply pending
runner.rollback_last()     # Undo most recent
runner.get_status()        # List applied/pending
```

14 migrations: initial schema, contracts, blobs, scope settings, search index, templates, tiering, events/webhooks, notifications, scheduling, sync state, composite indexes, row-level security, audit sequence uniqueness.

## Exceptions

All inherit from `ScopedError`. Key exceptions:

| Exception | When |
|-----------|------|
| `AccessDeniedError` | Principal cannot access object / rule denied |
| `IsolationViolationError` | Attempt to modify tombstoned object |
| `ScopeFrozenError` | Mutation attempted on frozen/archived scope |
| `ScopeNotFoundError` | Scope ID not found |
| `PrincipalNotFoundError` | Principal ID not found |
| `NoContextError` | Operation requires ScopedContext but none active |
| `TraceIntegrityError` | Audit hash chain broken |
| `AuditSequenceCollisionError` | Multi-process sequence collision after retries |
| `QuotaExceededError` | Resource creation would exceed quota (hard limit) |
| `RateLimitExceededError` | Action exceeds rate limit (soft limit) |
| `SecretAccessDeniedError` | Secret ref resolution denied |
| `ConnectorPolicyViolation` | Object type blocked by connector policy |
| `TenantResolutionError` | Cannot determine tenant (TenantRouter) |

## Testing Utilities

```python
from scoped.testing.base import ScopedTestCase
from scoped.testing.factory import ScopedFactory

class MyTest(ScopedTestCase):
    def test_something(self):
        alice = self.create_principal("Alice")
        with self.as_principal(alice):
            doc, _ = self.create_object("invoice", data={...})
```

pytest fixtures via conftest: `sqlite_backend`, `storage_backend` (parametrized SQLite + Postgres), `registry`.

## Project Structure

```
scoped/
  __init__.py              # Module-level proxy after scoped.init()
  client.py                # ScopedClient + init() + URL parsing
  types.py                 # Lifecycle, ActionType, URN, protocols
  exceptions.py            # Exception hierarchy (40+ classes)
  logging.py               # Structured JSON logging
  _namespaces/             # Simplified API (principals, objects, scopes, audit, secrets)
  identity/                # Layer 2: Principals + ScopedContext
  registry/                # Layer 1: URN registration
  objects/                 # Layer 3: Versioned objects + search
  tenancy/                 # Layer 4: Scopes, membership, projection, visibility
  rules/                   # Layer 5: Policy engine + rate limiting
  audit/                   # Layer 6: Hash-chained trail (writer + query)
  temporal/                # Layer 7: Rollback + reconstruction
  environments/            # Layer 8: Ephemeral workspaces
  flow/                    # Layer 9: Pipelines + promotions
  deployments/             # Layer 10: External graduation
  secrets/                 # Layer 11: Encrypted vault
  integrations/            # Layer 12: Plugin lifecycle
  connector/               # Layer 13: Federation + marketplace
  events/                  # Layer 14: Event bus + webhooks
  notifications/           # Layer 15: Notification engine
  scheduling/              # Layer 16: Scheduler + job queue
  storage/                 # Backend abstraction (SQLite, Postgres, TenantRouter)
    migrations/            # Schema migrations (m0001–m0013)
    tenant_router.py       # Database-per-tenant routing
  sync/                    # Management plane sync agent
  compliance/              # Invariant validation
  contrib/                 # Framework adapters
    django/                # Middleware, DRF integration, management commands
    fastapi/               # Middleware, dependencies, router
    flask/                 # Extension, admin blueprint
    mcp/                   # MCP server tools + resources
    otel.py                # OpenTelemetry instrumentation
  testing/                 # Test utilities, factories, assertions
  manifest/                # Service container wiring
```

## Database Schema (key tables)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `principals` | Identity entities | id, kind, display_name, lifecycle |
| `scoped_objects` | Versioned data objects | id, object_type, owner_id, current_version |
| `object_versions` | Immutable version snapshots | object_id, version, data_json, checksum |
| `scopes` | Sharing containers | id, name, owner_id, parent_scope_id, lifecycle |
| `scope_memberships` | Principal roles in scopes | scope_id, principal_id, role, lifecycle |
| `scope_projections` | Object visibility in scopes | scope_id, object_id, access_level |
| `rules` | Policy rules | name, type, effect, conditions, priority |
| `rule_bindings` | Rule application targets | rule_id, target_type, target_id |
| `audit_trail` | Hash-chained immutable log | sequence, actor_id, action, hash, previous_hash |
| `secrets` | Encrypted vault entries | id, name, owner_id (value never in this table) |
| `events` | Async event records | event_type, actor_id, target_type, data_json |
| `webhook_deliveries` | Outbound webhook tracking | event_id, endpoint_id, status, attempt_number |
| `jobs` | Scheduled job execution | action_type, state, result_json |
| `connectors` | Cross-org federation links | local_org_id, remote_org_id, state, direction |
