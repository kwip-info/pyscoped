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

After `scoped.init()`, all namespaces are available at module level: `scoped.principals`, `scoped.objects`, `scoped.scopes`, `scoped.audit`, `scoped.secrets`, `scoped.environments`. Or via client instance: `client.principals`, etc.

All actor parameters (`owner_id`, `principal_id`, `granted_by`, etc.) are **inferred from the active ScopedContext** when omitted. All parameters accepting objects also accept string IDs.

### scoped.principals

```python
create(display_name, *, kind="user", metadata=None, principal_id=None) -> Principal
get(principal_id) -> Principal                    # Raises PrincipalNotFoundError
find(principal_id) -> Principal | None
update(principal, *, display_name=None, metadata=None) -> Principal
archive(principal) -> Principal                   # Lifecycle → ARCHIVED
list(*, kind=None, limit=100, offset=0) -> list[Principal]
add_relationship(parent, child, *, relationship="member_of") -> PrincipalRelationship
relationships(principal, *, direction="both") -> list[PrincipalRelationship]
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

`data` accepts either `dict[str, Any]` or a registered typed instance (Pydantic model, dataclass, or `ScopedSerializable`). Typed instances are auto-serialized. `ObjectVersion.typed_data` deserializes back to the registered type.

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
members(scope, *, limit=100, offset=0) -> list[ScopeMembership]

# Projection (sharing)
project(obj, scope, *, projected_by=None, access_level="read") -> ScopeProjection
unproject(obj, scope, *, revoked_by=None) -> bool
projections(scope, *, limit=100, offset=0) -> list[ScopeProjection]

# Hierarchy
children(scope, *, limit=100) -> list[Scope]
ancestors(scope) -> list[Scope]                  # Immediate parent to root
descendants(scope, *, max_depth=10) -> list[Scope]  # BFS with depth cap
path(scope) -> list[Scope]                       # Root-to-scope path

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
count(*, actor_id=None, action=None, target_type=None, target_id=None) -> int
export(*, format="json"|"csv", **query_kwargs) -> str
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

## URN (Universal Resource Name)

Every registered construct gets a URN: `scoped:<kind>:<namespace>:<name>:<version>`. URNs are validated at construction:
- `kind`, `namespace`, `name` must be non-empty
- `version` must be >= 1
- Invalid URNs raise `ValueError` immediately

```python
from scoped.types import URN

urn = URN(kind="model", namespace="myapp", name="User", version=1)
str(urn)  # "scoped:model:myapp:User:1"
URN.parse("scoped:model:myapp:User:1")  # round-trips

URN(kind="", namespace="ns", name="X")   # ValueError: kind must be non-empty
URN(kind="m", namespace="ns", name="X", version=0)  # ValueError: version must be >= 1
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

## Rollback Preview

All three rollback modes support `dry_run=True` for previewing without modifying data:
```python
from scoped.temporal.rollback import RollbackExecutor, RollbackPreview

executor = RollbackExecutor(backend, audit_writer=writer)
preview = executor.rollback_action(trace_id, actor_id="alice", dry_run=True)
# RollbackPreview(would_rollback=('trace-1',), would_deny=(), entry_count=1)

preview = executor.rollback_cascade(root_id, actor_id="alice", dry_run=True)
# Shows all descendants that would be rolled back

# Then execute for real:
result = executor.rollback_action(trace_id, actor_id="alice")
```

## Environments (Layer 8)

Ephemeral, isolated workspaces for tasks. Each environment gets an auto-created isolation scope. Objects are tracked by origin (created inside vs. projected from outside). All mutations are owner-enforced and audited.

### Lifecycle

State machine: `SPAWNING → ACTIVE ↔ SUSPENDED → COMPLETED → DISCARDED | PROMOTED → DISCARDED`

```python
from scoped.environments.lifecycle import EnvironmentLifecycle

lifecycle = EnvironmentLifecycle(backend, audit_writer=writer)

# Spawn creates an isolation scope automatically
env = lifecycle.spawn(name="Review", owner_id=alice.id, metadata={"pr": 42})
env = lifecycle.activate(env.id, actor_id=alice.id)  # SPAWNING → ACTIVE

# Suspend / resume
env = lifecycle.suspend(env.id, actor_id=alice.id)   # ACTIVE → SUSPENDED
env = lifecycle.resume(env.id, actor_id=alice.id)     # SUSPENDED → ACTIVE

# Complete and decide: promote or discard
env = lifecycle.complete(env.id, actor_id=alice.id)   # ACTIVE → COMPLETED
env = lifecycle.promote(env.id, actor_id=alice.id)    # COMPLETED → PROMOTED
env = lifecycle.discard(env.id, actor_id=alice.id)    # → DISCARDED (archives scope, tombstones created objects)

# Templates
tmpl = lifecycle.create_template(name="Code Review", owner_id=alice.id, config={"mode": "review"})
env = lifecycle.spawn_from_template(tmpl.id, owner_id=alice.id)
```

All state transitions require `actor_id == env.owner_id` — raises `AccessDeniedError` otherwise. Every transition emits an audit trail entry.

### Object Container

```python
from scoped.environments.container import EnvironmentContainer

container = EnvironmentContainer(backend, audit_writer=writer)

# Track objects (environment must be ACTIVE)
container.add_object(env.id, obj.id, actor_id=alice.id)                          # origin=CREATED
container.project_in(env.id, external_obj.id, actor_id=alice.id)                 # origin=PROJECTED
container.remove_object(env.id, obj.id, actor_id=alice.id)

# Query
container.list_objects(env.id, origin=ObjectOrigin.CREATED)
container.get_created_object_ids(env.id)    # Promotion candidates
container.contains(env.id, obj.id)
container.count(env.id)
```

When `actor_id` is provided, ownership is enforced and audit entries are emitted. Discard cascade: objects with `origin=CREATED` are tombstoned; projected objects are left untouched.

### Snapshots

```python
from scoped.environments.snapshot import SnapshotManager

snapshots = SnapshotManager(backend, audit_writer=writer)

# Capture full state (env record, objects, versions, memberships)
snap = snapshots.capture(env.id, created_by=alice.id, name="v1")
assert snapshots.verify(snap.id)  # SHA-256 checksum validation

# Restore to a previous snapshot
snapshots.restore(snap.id, restored_by=alice.id)
# Resets current_version pointers + syncs environment_objects rows

snapshots.list_snapshots(env.id)  # Newest first

# Retention — prune old snapshots
snapshots.apply_retention(env.id, max_snapshots=5)       # Keep newest 5
snapshots.apply_retention(env.id, max_age_days=30)       # Delete older than 30 days
```

Capture and restore both enforce owner access and emit audit records.

### Namespace API

After `scoped.init()`, environments are available at `scoped.environments` with context-aware defaults:

```python
with scoped.as_principal(alice):
    env = scoped.environments.spawn("Review", metadata={"pr": 42})
    scoped.environments.activate(env)

    scoped.environments.add_object(env, obj)
    snap = scoped.environments.capture(env, name="v1")

    scoped.environments.complete(env)
    scoped.environments.restore(snap.id)
    scoped.environments.discard(env)
```

### Rule engine integration

Rules can be bound to specific environments via `BindingTargetType.ENVIRONMENT`. The container evaluates rules before `add_object()` when a `rule_engine` is provided (automatic via `ScopedServices`).

### Temporal support

Environment state transitions are rollbackable via Layer 7. `RollbackExecutor` restores `state` from `before_state` on transition rollback and marks environments `discarded` on spawn rollback.

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

### Rule engine caching
```python
# Opt-in TTL-based cache (default: no cache)
engine = RuleEngine(backend, cache_ttl=60.0)  # 60s TTL
# Subsequent evaluate() calls use cached rules — 0 DB queries on cache hit
# Cache auto-invalidated on create_rule/update_rule/archive_rule/bind_rule/unbind_rule
print(engine._cache.stats())  # {"hits": 42, "misses": 3, "hit_rate": 0.93, ...}
```

### Rule evaluation debugging
```python
explanation = engine.evaluate_with_explanation(
    action="create", principal_id="alice", object_type="invoice",
)
print(explanation.summary)  # "Denied by rule 'block-invoices' (priority 100)"
for exp in explanation.explanations:
    print(f"  {exp.rule.name}: matched={exp.matched}, reason={exp.reason}")
    for cm in exp.condition_matches:
        print(f"    {cm.condition_key}: expected={cm.expected}, actual={cm.actual}, matched={cm.matched}")
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

### SQLAlchemy Core backends (new in 0.7.0)
Drop-in replacements that use SQLAlchemy Core for connection management and schema creation:
```python
from scoped.storage.sa_sqlite import SASQLiteBackend
from scoped.storage.sa_postgres import SAPostgresBackend

backend = SASQLiteBackend(":memory:")           # or SASQLiteBackend("/path/to/db.sqlite3")
backend = SAPostgresBackend("postgresql://user:pass@host/db", pool_size=5, enable_rls=True)
```

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

### SQLAlchemy Core query building
All 16 layers build queries using SQLAlchemy Core constructs compiled to raw SQL via `compile_for()`:
```python
from scoped.storage._schema import principals
from scoped.storage._query import compile_for

stmt = sa.select(principals).where(principals.c.id == principal_id)
sql, params = compile_for(stmt, backend.dialect)
row = backend.fetch_one(sql, params)
```
Tables defined in `scoped.storage._schema` (63 `sa.Table` objects). `compile_for(stmt, dialect)` returns `(sql_string, params_tuple)`. `dialect_insert(table, dialect)` returns dialect-aware INSERT with `on_conflict_do_update()` support.

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

### Django ScopedModel (new in 0.7.0)
Abstract base model that auto-syncs with pyscoped's object layer:
```python
from scoped.contrib.django.models import ScopedModel

class Invoice(ScopedModel):
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)

    class ScopedMeta:
        object_type = "invoice"
        scoped_fields = ["amount", "currency"]  # None = all fields
```
- `save()` atomically persists to Django + creates/updates ScopedObject
- `delete()` atomically tombstones + deletes Django row
- `Invoice.scoped_objects.for_principal(pid)` filters by pyscoped visibility
- `scoped_context_for(principal_id)` context manager for management commands/Celery

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

Six core modules emit structured logs automatically:
- `scoped.objects.manager` — create, update, tombstone (INFO)
- `scoped.audit.writer` — record (DEBUG)
- `scoped.rules.engine` — evaluate (DEBUG)
- `scoped.secrets.vault` — create, rotate, resolve (INFO, never logs values)
- `scoped.tenancy.lifecycle` — create_scope, freeze, archive (INFO)
- `scoped.sync.transport` — push_batch (INFO)

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

### Audit retention
```python
from scoped.audit.retention import AuditRetention, RetentionPolicy

retention = AuditRetention(backend)
policy = RetentionPolicy(max_age_days=90, compact_after_state=True)
estimate = retention.estimate(policy)    # How many entries would be affected
result = retention.apply(policy)         # Delete old + compact state columns
# Hash chain integrity preserved after compaction (hashes don't depend on state)
```

14 migrations: initial schema, contracts, blobs, scope settings, search index, templates, tiering, events/webhooks, notifications, scheduling, sync state, composite indexes, row-level security, audit sequence uniqueness.

## Config Inheritance Transparency

Per-scope settings inherit from parent scopes. `ConfigResolver.resolve()` returns a `ResolvedSetting` with a `resolution_chain` showing all ancestor values:

```python
from scoped.tenancy.config import ConfigResolver

resolver = ConfigResolver(backend)

# Root sets theme=dark, child overrides to light
result = resolver.resolve(child_scope_id, "theme")
result.value                # "light" (winner = closest to queried scope)
result.inherited            # False (set directly on child)
result.resolution_chain     # [(root_id, "dark"), (child_id, "light")]

# resolve_all() also populates chains
all_settings = resolver.resolve_all(child_scope_id)
all_settings["theme"].resolution_chain  # [(root_id, "dark"), (child_id, "light")]
```

## Blob Streaming

Store and read binary content without loading entire blobs into memory:

```python
from scoped.objects.blobs import BlobManager

# Stream upload with incremental SHA-256
with open("large.bin", "rb") as fp:
    ref = manager.store_stream(
        fp=fp, filename="large.bin",
        content_type="application/octet-stream", owner_id=alice.id,
    )

# Stream download (Iterator[bytes])
for chunk in manager.read_stream(ref.id, principal_id=alice.id):
    output.write(chunk)
```

Backend implementations:
- `InMemoryBlobBackend` — single-chunk (for tests)
- `LocalBlobBackend` — 64KB chunked read/write

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

pytest fixtures via conftest: `sqlite_backend`, `sa_sqlite_backend`, `storage_backend` (parametrized SQLite + SA SQLite + Postgres), `registry`.

### Exportable fixtures (`scoped.testing.fixtures`)
- `scoped_backend` — in-memory SQLite backend
- `scoped_services` — fully-wired `ScopedServices`
- `scoped_txn` — wraps test in a transaction, rolls back at teardown
- `alice`, `bob` — pre-built test principals
- `sample_object(owner, type, data)`, `sample_scope(owner, name, members)` — factory fixtures

### Assertion helpers (`scoped.testing.assertions`)
```python
from scoped.testing.assertions import (
    assert_access_denied,    # Verify fn raises AccessDeniedError
    assert_can_read,         # Verify principal can read object
    assert_cannot_read,      # Verify principal cannot read object
    assert_trace_exists,     # Verify audit entry with flexible criteria
    assert_isolated,         # Verify owner-private isolation
    assert_audit_recorded,   # Verify specific audit entry
    assert_version_count,    # Verify object version count
    assert_hash_chain_valid, # Verify audit hash chain
    assert_tombstoned,       # Verify object is soft-deleted
    assert_secret_never_leaked,  # Verify secret not in audit
)
```

### Backend markers (`scoped.testing.markers`)
```python
from scoped.testing.markers import sqlite_only, postgres_only

@sqlite_only
def test_fts5_search(): ...

@postgres_only
def test_rls_policies(): ...
```

## Typed IDs

All entity IDs are thin `str` subclasses for static type safety with zero runtime overhead:
```python
from scoped.ids import PrincipalId, ObjectId, ScopeId

pid = PrincipalId.generate()   # PrincipalId (is-a str)
oid = ObjectId.generate()      # ObjectId  (is-a str)
isinstance(pid, str)           # True — fully backward compatible
```

Available types: `PrincipalId`, `ObjectId`, `VersionId`, `ScopeId`, `MembershipId`, `ProjectionId`, `RuleId`, `BindingId`, `TraceId`, `EntryId`, `SecretId`, `ConnectorId`, `ScheduleId`, `JobId`. All re-exported from `scoped.types`.

## Typed Rule Conditions

Rule conditions are validated at creation time via Pydantic models:
```python
from scoped.rules.conditions import AccessCondition, QuotaCondition, QuotaSpec

# Typed (validated immediately)
cond = AccessCondition(action=["create", "read"], object_type="invoice")
rule = client.services.rules.create_rule(name="allow-invoices", ..., conditions=cond)

# Raw dict still works (backward compatible)
rule = client.services.rules.create_rule(..., conditions={"action": "create"})

# Typed access on existing rules
rule.typed_conditions  # AccessCondition(action="create", ...)
```

Models: `AccessCondition`, `RateLimitCondition` (+ `RateLimitSpec`), `QuotaCondition` (+ `QuotaSpec`), `RedactionCondition` (+ `RedactionSpec`), `FeatureFlagCondition` (+ `FeatureFlagSpec`).

## Typed Object Protocol

Register types for auto-serialization/deserialization of versioned object data:
```python
from pydantic import BaseModel
import scoped

class Invoice(BaseModel):
    amount: float
    currency: str
    status: str = "draft"

scoped.register_type("invoice", Invoice)

# Create with typed data (auto-serializes)
doc, v1 = scoped.objects.create("invoice", data=Invoice(amount=500, currency="USD"))

# Read with typed access
versions = scoped.objects.versions(doc.id)
invoice = versions[0].typed_data  # Invoice(amount=500, ...)

# Dict path still works (backward compatible)
doc, v2 = scoped.objects.create("invoice", data={"amount": 500, "currency": "USD"})
```

Supported types: Pydantic `BaseModel`, `@dataclass`, `ScopedSerializable` protocol. Type registry is thread-safe.

## Stability Markers

APIs decorated with `@experimental` or `@preview` emit a one-time warning on first use:
```python
from scoped._stability import experimental, preview, stable, get_stability_level

@experimental(reason="API surface may change")
class MyService: ...

@preview(reason="Nearing stable, feedback welcome")
class MyConnector: ...
```
- `ExperimentalAPIWarning(FutureWarning)` — Layers 8-16 (environments, flow, deployments, secrets, integrations, events, notifications, scheduling)
- `PreviewAPIWarning(FutureWarning)` — Layer 13 connector/marketplace
- Suppress via `warnings.filterwarnings("ignore", category=ExperimentalAPIWarning)`

## Project Structure

```
scoped/
  __init__.py              # Module-level proxy after scoped.init()
  client.py                # ScopedClient + init() + URL parsing
  ids.py                   # Typed ID classes (PrincipalId, ObjectId, ScopeId, etc.)
  types.py                 # Lifecycle, ActionType, URN, ScopedSerializable, re-exports ids
  exceptions.py            # Exception hierarchy (40+ classes)
  logging.py               # Structured JSON logging
  _stability.py            # @experimental, @preview, @stable decorators
  _type_registry.py        # Typed Object Protocol — register_type, serialize, deserialize
  _type_adapters.py        # Pydantic, dataclass, ScopedSerializable adapters
  _namespaces/             # Simplified API (principals, objects, scopes, audit, secrets)
  identity/                # Layer 2: Principals + ScopedContext
  registry/                # Layer 1: URN registration
  objects/                 # Layer 3: Versioned objects + search
  tenancy/                 # Layer 4: Scopes, membership, projection, visibility
  rules/                   # Layer 5: Policy engine + rate limiting + typed conditions
  audit/                   # Layer 6: Hash-chained trail (writer + query)
  temporal/                # Layer 7: Rollback + reconstruction
  environments/            # Layer 8: Ephemeral workspaces (@experimental)
  flow/                    # Layer 9: Pipelines + promotions (@experimental)
  deployments/             # Layer 10: External graduation (@experimental)
  secrets/                 # Layer 11: Encrypted vault (@experimental)
  integrations/            # Layer 12: Plugin lifecycle (@experimental)
  connector/               # Layer 13: Federation + marketplace (@preview)
  events/                  # Layer 14: Event bus + webhooks (@experimental)
  notifications/           # Layer 15: Notification engine (@experimental)
  scheduling/              # Layer 16: Scheduler + job queue (@experimental)
  storage/                 # Backend abstraction (SQLite, Postgres, TenantRouter)
    _schema.py             # 63 SQLAlchemy Core Table definitions for query building
    _query.py              # compile_for(), dialect_insert() — SA Core → raw SQL bridge
    sa_sqlite.py           # SASQLiteBackend (SQLAlchemy Core-backed)
    sa_postgres.py         # SAPostgresBackend (SQLAlchemy Core-backed)
    migrations/            # Schema migrations (m0001–m0014)
    tenant_router.py       # Database-per-tenant routing
  sync/                    # Management plane sync agent
  contrib/                 # Framework adapters
    django/                # Middleware, DRF, ScopedModel, management commands
      models.py            # ScopedModel, ScopedQuerySet, ScopedDjangoManager
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
