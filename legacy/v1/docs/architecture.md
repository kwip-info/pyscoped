---
title: Architecture
description: Deep dive into the pyscoped 16-layer architecture, core invariants, isolation model, versioning, audit chain, and service wiring.
category: architecture
---

# Architecture

This document describes how pyscoped is designed, why each decision was made,
and how the layers compose into a single coherent system.

---

## Design Philosophy

pyscoped is built on three principles:

**Compliance-first.** Security, auditability, and isolation are not features
bolted on after the fact. They are structural properties of the framework.
Every layer is designed so that doing the wrong thing is harder than doing the
right thing. You cannot create an object without a principal. You cannot share
without an explicit projection. You cannot mutate without producing a trace.

**Defense-in-depth.** No single mechanism is trusted to enforce isolation.
Application-layer checks in `ScopedManager` are backed by scope-based
visibility rules, which can be further backed by Postgres row-level security,
which can be further backed by database-per-tenant routing. Each tier is
independent — if one fails, the next catches it.

**Append-only.** State is never overwritten. Every mutation creates a new
version. Every action appends to the audit trail. Tombstones replace deletions.
This makes the system fully auditable and fully reversible to any point in time.

---

## The 16-Layer Model

Every layer depends only on the layers below it. The dependency chain is
strict — there are no circular dependencies, no optional layers, and no
shortcuts.

### Layer 0: Compliance

**Responsibility:** Validates every invariant across all other layers at both
test time (static analysis) and runtime (middleware enforcement).

**Depends on:** All layers (wraps from the outside).

The compliance engine provides `ScopedTestCase` for testing, `ComplianceAuditor`
for static invariant checks, `ComplianceMiddleware` for runtime enforcement,
and `IsolationFuzzer` for randomized access pattern testing. If a construct is
not registered, an action does not produce a trace, or an object leaks outside
its scope, compliance catches it.

### Layer 1: Registry

**Responsibility:** Universal construct registration. Every entity in the
system — principals, objects, scopes, rules, secrets, connectors — must have a
registry entry with a URN and lifecycle state.

**Depends on:** Storage backend.

The registry is the foundation. Without it, nothing can be referenced,
discovered, or lifecycle-managed. Registry entries track creation time,
lifecycle state (`DRAFT`, `ACTIVE`, `DEPRECATED`, `ARCHIVED`), and the
construct's URN.

### Layer 2: Identity

**Responsibility:** Principals (users, teams, services, agents) and the
`ScopedContext` that identifies who is acting.

**Depends on:** Layer 1 (Registry).

Every operation requires a `ScopedContext` with an acting principal. The
identity layer provides the `PrincipalStore` for CRUD, the `ScopedContext`
context manager for declaring the actor, and resolution utilities. Identity is
generic — the framework provides the machinery, not the concrete types. The
`kind` field is application-defined.

### Layer 3: Objects

**Responsibility:** Versioned, isolation-enforced data objects. Every mutation
creates a new `ObjectVersion`. Default visibility is creator-only.

**Depends on:** Layer 2 (Identity), Layer 1 (Registry).

The `ScopedManager` is the gatekeeper. It enforces `owner_id` checks on every
read and write. Objects are tombstoned, never deleted. Version history is
immutable and retained indefinitely.

### Layer 4: Tenancy

**Responsibility:** Scopes, membership, and projections — the sharing
primitive.

**Depends on:** Layer 3 (Objects), Layer 2 (Identity).

To share an object, the owner creates a scope, adds members with roles
(`viewer`, `editor`, `admin`, `owner`), and projects the object in. Scopes can
nest — child scope members inherit visibility from parent scopes. Scopes
themselves are registered, versioned, and audited constructs.

### Layer 5: Rules

**Responsibility:** Policy engine with deny-overrides semantics.

**Depends on:** Layer 4 (Tenancy), Layer 3, Layer 2, Layer 1.

Rules modify what the scoping engine allows. They bind to scopes, principals,
object types, or specific objects. The evaluation model is:
any `DENY` = denied; at least one `ALLOW` and no `DENY` = allowed. Rules are
versioned — changing a rule is a traced, rollbackable action.

### Layer 6: Audit

**Responsibility:** Hash-chained, append-only audit trail.

**Depends on:** All layers above (wraps every operation).

Every action through every layer produces an immutable `TraceEntry` with a
SHA-256 hash linking to its predecessor. The audit trail is the backbone of
compliance. Entries record the actor, action, target, before/after state,
scope context, and timestamp. The `AuditWriter` is thread-safe with a mutex
protecting sequence numbering and hash chaining.

### Layer 7: Temporal

**Responsibility:** Point-in-time rollback and state reconstruction.

**Depends on:** Layer 6 (Audit).

Because every action is traced and every object is versioned, any state can be
reconstructed at any point in time. The `RollbackExecutor` can reverse a single
action or roll back all actions after a timestamp. Rollbacks are themselves
traced — you get full auditability of the undo.

### Layer 8: Environments

**Responsibility:** Ephemeral and persistent workspaces.

**Depends on:** Layer 4 (Tenancy), Layer 3 (Objects).

Environments are the collaborative workspace — the ephemeral context where
throwaway work happens. Each environment gets its own isolation scope.
Everything inside is invisible outside unless explicitly projected out.

### Layer 9: Flow

**Responsibility:** Stages, pipelines, promotions, and flow channels.

**Depends on:** Layer 8 (Environments), Layer 4 (Tenancy).

Flow defines how information moves through the system — stages in pipelines
(draft, review, approved, deployed), channels between environments and scopes,
and promotions from ephemeral to persistent. This is the "river" in pyscoped's
conceptual model.

### Layer 10: Deployments

**Responsibility:** Graduation to external targets with gate checks.

**Depends on:** Layer 9 (Flow).

Deployments are the final flow — from the pyscoped world into an external
target. Gate checks enforce that all stages passed, all rules are satisfied,
and all approvals are collected before graduation.

### Layer 11: Secrets

**Responsibility:** Encrypted vault with zero-trust access via refs.

**Depends on:** Layer 3 (Objects), Layer 4 (Tenancy), Layer 6 (Audit).

Secrets are the highest-security objects. They are encrypted at rest using
Fernet (AES-128-CBC + HMAC-SHA256), accessed via ref tokens that are
scope-checked on every dereference, and never serialized in plaintext. Secret
values never appear in audit state, environment snapshots, or connector traffic.

### Layer 12: Integrations and Plugins

**Responsibility:** Sandboxed extensions, hooks, and external system
connections.

**Depends on:** Layer 11 (Secrets), Layer 1 (Registry), Layer 5 (Rules).

External systems connect through registered integrations with scoped
credentials (secret refs). Plugins extend the framework through sandboxed hooks
with declared permissions. Both are first-class scoped citizens.

### Layer 13: Connector and Marketplace

**Responsibility:** Cross-organization federation and public discovery.

**Depends on:** All layers.

Connectors bridge two separate pyscoped instances under mutual agreement.
Marketplace provides public discovery for connector templates, plugins, and
integrations. This is where the river metaphor reaches its full expression —
canals between watersheds.

### Layer 14: Events and Webhooks

**Responsibility:** Asynchronous, scoped event bus with outbound/inbound
webhook delivery.

**Depends on:** Layer 6 (Audit), Layer 4 (Tenancy).

The `EventBus` emits typed events, routes them to in-process listeners, and
persists them for webhook delivery. `WebhookDelivery` handles outbound HTTP
delivery with retry and exponential backoff.

### Layer 15: Notifications

**Responsibility:** Principal-targeted messages generated from events and
rules.

**Depends on:** Layer 14 (Events), Layer 5 (Rules).

The `NotificationEngine` processes events through notification rules to
generate messages for specific principals. Notifications have read/unread
state, filtering, and scope-aware visibility.

### Layer 16: Scheduling and Jobs

**Responsibility:** Time-based actions, recurring schedules, and scoped job
execution.

**Depends on:** Layer 6 (Audit), Layer 2 (Identity).

The `Scheduler` manages recurring schedules and one-off actions. The `JobQueue`
executes actions with result tracking, retry, and audit integration. All jobs
run within a principal context.

---

## The 10 Core Invariants

These are absolute. The compliance engine enforces every one.

### 1. Nothing exists without registration

Every construct — data, code, behavioral — must have a registry entry with a
URN and lifecycle state. **Why:** Without a central registry, constructs can
exist in the system without being discoverable, auditable, or
lifecycle-managed. The registry is the single source of truth for "what exists."

### 2. Nothing happens without identity

Every operation must have a `ScopedContext` identifying the acting principal.
No anonymous actions. **Why:** Accountability requires attribution. If you
cannot determine who performed an action, you cannot audit it, you cannot
enforce access control, and you cannot roll it back to the correct actor.

### 3. Nothing is shared by default

Every object starts creator-private. Sharing requires explicit scope creation,
membership grants, and object projection. **Why:** Secure defaults prevent
accidental exposure. It is safer to require explicit sharing than to require
explicit restriction. Data leaks happen when the default is open.

### 4. Nothing happens without a trace

Every action — including reads, access checks, and rule evaluations — produces
an immutable, hash-chained audit entry. **Why:** Compliance requires a complete
and tamper-evident record. The hash chain ensures that entries cannot be
modified or deleted without detection. If it did not produce a trace, it did
not happen.

### 5. Nothing is truly deleted

Objects are tombstoned, not removed. Versions are retained. Audit entries are
append-only. **Why:** Deletion destroys evidence. Regulatory frameworks require
retention. Rollback requires the full history to exist. Tombstoning preserves
the record while marking it as logically removed.

### 6. Deny always wins

When rules conflict, DENY overrides ALLOW. **Why:** Security is the default;
access must be explicitly granted. If a DENY and an ALLOW rule both match, the
safe behavior is denial. This prevents privilege escalation through rule
accumulation.

### 7. Revocation is immediate

When access is revoked, it takes effect within the same transaction. No
eventual consistency for security. **Why:** A revocation that takes effect
"eventually" leaves a window where the revoked party still has access. For
security-critical operations, the revocation must be atomic with the action
that triggered it.

### 8. Everything is versioned

Objects, rules, scopes, secrets — every mutation creates a new version. Old
versions are retained. **Why:** Versioning enables audit, diff, rollback, and
point-in-time reconstruction. In-place mutation destroys the previous state,
making it impossible to answer "what did this look like yesterday?"

### 9. Everything is rollbackable

Any action can be reversed to any point in time. Rollbacks are themselves
traced and rule-governed. **Why:** Mistakes happen. Rollback is the safety net.
Because every action is traced and every state is versioned, reversal is always
possible. The rollback itself is an audited action, maintaining the integrity
of the trail.

### 10. Secrets never leak

Secret values never appear in audit trails, environment snapshots, or connector
traffic. Access is via refs that are scope-checked on every dereference.
**Why:** Secrets in logs, traces, or snapshots become uncontrollable. Ref-based
access with per-dereference authorization ensures that secret exposure is
always intentional and always audited.

---

## Isolation Model

pyscoped enforces isolation at three independent tiers. Each tier operates
independently — if one fails, the next catches it.

### Tier 1: Application Layer (Layer 3)

Every query routes through `ScopedManager`, which enforces
`owner_id == principal_id` on every read and write. There is no bypass without
going directly to the raw storage backend (which production code never does).

```python
# ScopedManager.get() checks owner_id internally:
obj = manager.get(object_id, principal_id=alice.id)
# Returns None if alice.id != obj.owner_id (unless scope visibility applies)
```

### Tier 2: Scope-Based Visibility (Layer 4)

Objects become visible to non-owners only through scope projections combined
with membership:

1. **Owner sees their objects** — always, unconditionally.
2. **Scope members see projected objects** — explicit opt-in by the owner.
3. **Child scope members inherit visibility** — from parent scopes.
4. **DENY rules restrict further** — Layer 5 can override scope visibility.

### Tier 3: Postgres Row-Level Security

Database-enforced isolation that operates below the application layer. Even if
application code has a bug, the database itself enforces that principals can
only see their own rows. See the [Security docs](security.md) for full details.

---

## Visibility Resolution

When a principal requests an object, the system resolves visibility through
this chain:

1. **Owned objects.** If `object.owner_id == principal_id`, access is granted.
   This is always checked first and always succeeds for the owner.

2. **Scope projections.** The system checks whether the object is projected
   into any scope where the requesting principal is an active member with
   sufficient access level.

3. **Hierarchy inheritance.** If the object is projected into a parent scope,
   members of child scopes inherit visibility (subject to access level
   propagation rules).

4. **Rule overrides.** The rules engine evaluates any bound rules. DENY rules
   can revoke visibility even if scope membership would otherwise grant it.
   This is the final gate — deny always wins.

---

## Versioning Model

Every mutation in pyscoped creates a new `ObjectVersion`. The object record
tracks the current version number; individual versions are immutable rows.

```
ScopedObject (id="doc-1", current_version=3)
  ├── ObjectVersion (version=1, data={...}, checksum="abc...")
  ├── ObjectVersion (version=2, data={...}, checksum="def...")
  └── ObjectVersion (version=3, data={...}, checksum="ghi...")
```

Key properties:

- **No in-place updates.** The `data_json` column in `object_versions` is
  write-once. Updates increment `current_version` on the parent object and
  insert a new version row.
- **Checksums.** Each version includes a checksum of its data for integrity
  verification.
- **Change reasons.** Every version can carry a `change_reason` string
  explaining why the change was made.
- **Creator attribution.** Every version records `created_by` — the principal
  who made the change.
- **Tombstones.** Soft-deletion sets a tombstone record on the object. The
  object and all versions remain in the database.

---

## Audit Chain

The audit trail is a SHA-256 hash chain — a sequence of `TraceEntry` records
where each entry's hash incorporates the hash of the previous entry.

### Structure of a TraceEntry

| Field | Description |
|-------|-------------|
| `id` | Unique entry identifier |
| `sequence` | Monotonically increasing integer |
| `actor_id` | Principal who performed the action |
| `action` | `ActionType` enum (CREATE, UPDATE, DELETE, etc.) |
| `target_type` | Type of the affected construct |
| `target_id` | ID of the affected construct |
| `timestamp` | UTC timestamp |
| `hash` | SHA-256 hash of this entry (including `previous_hash`) |
| `previous_hash` | Hash of the preceding entry |
| `scope_id` | Scope context (if applicable) |
| `before_state` | State before the action (for updates/deletes) |
| `after_state` | State after the action (for creates/updates) |

### Hash computation

The hash of each entry is computed over: sequence number, actor_id, action,
target_type, target_id, timestamp, previous_hash, and a canonical
serialization of the before/after state. This means:

- **Insertion detection.** Inserting an entry changes all subsequent hashes.
- **Deletion detection.** Removing an entry breaks the chain at that point.
- **Modification detection.** Changing any field changes the entry's hash and
  breaks the chain from that point forward.

### Chain verification

```python
verification = scoped.audit.verify()
# Returns: ChainVerification(valid=True, entries=1042, first_seq=1, last_seq=1042)
```

For large audit trails, use chunked verification with sequence ranges:

```python
# Verify in chunks of 1000
chunk_1 = scoped.audit.verify(from_sequence=1, to_sequence=1000)
chunk_2 = scoped.audit.verify(from_sequence=1001, to_sequence=2000)
```

---

## Service Container

`ScopedServices` is the central wiring point for all 16 layers. It takes a
storage backend and an `AuditWriter`, then provides lazy-initialized access to
every service in the framework.

```python
from scoped.manifest._services import build_services

services = build_services(backend)

# Services are lazy — instantiated on first access
principals = services.principals      # PrincipalStore
manager = services.manager            # ScopedManager (with RuleEngine injected)
scopes = services.scopes              # ScopeLifecycle
projections = services.projections    # ProjectionManager
rules = services.rules                # RuleStore
rule_engine = services.rule_engine    # RuleEngine
environments = services.environments  # EnvironmentLifecycle
pipelines = services.pipelines        # PipelineManager
flow = services.flow                  # FlowEngine
deployments = services.deployments    # DeploymentExecutor
secrets = services.secrets            # SecretVault
plugins = services.plugins            # PluginLifecycleManager
connectors = services.connectors      # ConnectorManager
events = services.events              # EventBus
notifications = services.notifications  # NotificationEngine
scheduler = services.scheduler        # Scheduler
```

**Lazy initialization** means the cost of importing and constructing each
service is deferred until first use. If your application only uses objects and
scopes, the secrets vault, connector manager, and scheduler are never
instantiated.

The `ScopedClient` wraps `ScopedServices` and exposes simplified namespace
objects (`client.objects`, `client.scopes`, etc.) that provide context-aware
defaults and a streamlined API.

---

## Context System

`ScopedContext` is implemented with Python's `contextvars` module, making it
both thread-safe and async-safe without any additional configuration.

```python
from scoped.identity.context import ScopedContext

# Set the acting principal for a block
with ScopedContext(principal=alice):
    ctx = ScopedContext.current()
    print(ctx.principal_id)    # alice.id
    print(ctx.principal_kind)  # "user"
```

### How it works

- `ScopedContext.__enter__` calls `ContextVar.set()`, which returns a token.
- `ScopedContext.__exit__` calls `ContextVar.reset(token)`, restoring the
  previous value.
- Nesting is automatic — each `with` block saves and restores the prior
  context.

### Thread safety

`contextvars` are per-thread by default in CPython. Each thread has its own
context stack, so concurrent operations in different threads do not interfere.

### Async safety

`contextvars` are natively supported by Python's `asyncio`. When an async task
is scheduled, it inherits a copy of the current context. This means
`ScopedContext` works correctly in async frameworks (FastAPI, aiohttp) without
modification.

### Extras

`ScopedContext` accepts arbitrary keyword arguments as `extras`, which
downstream layers can consume:

```python
with ScopedContext(principal=alice, environment_id="env-123", scope_id="team-1"):
    ctx = ScopedContext.current()
    print(ctx.extras["environment_id"])  # "env-123"
```

### Class-level accessors

| Method | Behavior |
|--------|----------|
| `ScopedContext.current()` | Returns active context; raises `NoContextError` if none |
| `ScopedContext.current_or_none()` | Returns active context or `None` |
| `ScopedContext.current_principal()` | Shortcut for `current().principal` |
| `ScopedContext.require()` | Alias for `current()` — reads well at call sites |
