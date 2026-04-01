---
title: Security & Isolation
description: Comprehensive guide to pyscoped's defense-in-depth isolation model, rules engine, Postgres RLS, secret handling, and cryptographic primitives.
category: security
---

# Security & Isolation

pyscoped enforces isolation at multiple independent layers. This document
covers every mechanism, how they compose, and the cryptographic primitives
underlying them.

---

## Isolation Enforcement

The `ScopedManager` is the primary gatekeeper for all object operations. Every
read and write passes through it, and it enforces `owner_id` checks before any
data is returned or modified.

### How it works

When `ScopedManager.get()` is called, it loads the object from the storage
backend and then verifies that the requesting principal is the owner:

```python
with scoped.as_principal(alice):
    # Internally: load object, check obj.owner_id == alice.id
    doc = scoped.objects.get(doc_id)
```

If the principal is not the owner and no scope projection grants visibility,
the method returns `None`. There is no "admin bypass" in the manager — even
system principals are subject to the same checks unless they own the object.

### Write isolation

Creates, updates, and tombstones all verify ownership:

```python
with scoped.as_principal(bob):
    # Raises AccessDeniedError — Bob does not own this object
    scoped.objects.update(alice_doc.id, data={...})
```

The rules engine (Layer 5) is evaluated before every `create()`, `update()`,
and `tombstone()` operation. If a bound DENY rule matches, the operation is
rejected with `AccessDeniedError` before any data is written.

---

## Scope-Based Sharing

Objects are shared through the explicit combination of three actions:

1. **Create a scope** — a named isolation boundary.
2. **Add members** — grant principals a role within the scope.
3. **Project an object** — make it visible within the scope.

All three steps must happen explicitly. There is no implicit sharing, no
default visibility, and no "public" mode.

### Membership roles

| Role | Capabilities |
|------|-------------|
| `viewer` | Read projected objects |
| `editor` | Read and write projected objects (where access level permits) |
| `admin` | Manage members, manage projections, read/write objects |
| `owner` | Full control — rename scope, freeze, archive, all admin capabilities |

### Access levels on projections

When projecting an object into a scope, the owner specifies an access level:

```python
scoped.scopes.project(doc, team, access_level="read")   # Members can read
scoped.scopes.project(doc, team, access_level="write")  # Members can read + write
scoped.scopes.project(doc, team, access_level="admin")  # Full object access
```

### Revocation

Revocation is immediate (Invariant 7). When a projection is revoked or a
member is removed, the change takes effect in the same transaction:

```python
scoped.scopes.unproject(doc, team)           # Immediate — no grace period
scoped.scopes.remove_member(team, bob)       # Immediate — Bob loses all access
```

### Scope lifecycle

Scopes have lifecycle states that affect what operations are permitted:

- **ACTIVE** — normal operation.
- **FROZEN** — no new members or projections can be added; existing access is
  preserved. Use `scoped.scopes.freeze(scope)` to freeze.
- **ARCHIVED** — soft-deleted. All access is revoked, all projections are
  removed. Use `scoped.scopes.archive(scope)` to archive.

Attempting to add members or projections to a frozen scope raises
`ScopeFrozenError`.

---

## Rules Engine

The rules engine uses a **deny-overrides** model. This is a deliberate design
choice: security is the default, and access must be explicitly granted.

### Evaluation semantics

- **Any DENY rule matches** -> access denied, regardless of ALLOW rules.
- **At least one ALLOW rule matches and no DENY rules match** -> access granted.
- **No rules are bound** -> access is granted (baseline behavior is governed by
  scope membership and ownership, not rules).

### Rule types

| Type | Purpose |
|------|---------|
| `ACCESS` | Controls whether a principal can perform an action on a target |

Rules are created with conditions that specify what they match:

```python
from scoped.rules.models import RuleType, RuleEffect, BindingTargetType

# DENY rule: block external principals from creating invoices
rule = client.services.rules.create_rule(
    name="block-external-invoices",
    rule_type=RuleType.ACCESS,
    effect=RuleEffect.DENY,
    conditions={
        "action": ["create"],
        "object_type": ["invoice"],
        "principal_kind": ["external"],
    },
    priority=100,
    created_by=admin.id,
)
```

### Binding targets

Rules are inert until bound to a target. Binding determines where the rule is
evaluated:

| Target type | Effect |
|-------------|--------|
| `SCOPE` | Rule applies to all operations within the scope |
| `PRINCIPAL` | Rule applies to a specific principal |
| `OBJECT_TYPE` | Rule applies to all objects of a given type |
| `OBJECT` | Rule applies to a specific object instance |

```python
# Bind to a scope — applies to all operations in this scope
client.services.rules.bind_rule(
    rule.id,
    target_type=BindingTargetType.SCOPE,
    target_id=team_scope.id,
    bound_by=admin.id,
)

# Bind to an object type — applies globally for this type
client.services.rules.bind_rule(
    rule.id,
    target_type=BindingTargetType.OBJECT_TYPE,
    target_id="invoice",
    bound_by=admin.id,
)
```

### Enforcement in ScopedManager

The `RuleEngine` is injected into `ScopedManager` via the `ScopedServices`
container. Before every `create()`, `update()`, and `tombstone()` operation,
the manager calls `rule_engine.evaluate()`. If the result is denied, the
operation raises `AccessDeniedError` before any data is written:

```python
with scoped.as_principal(external_user):
    # Rule "block-external-invoices" fires -> AccessDeniedError
    scoped.objects.create("invoice", data={"amount": 500})
```

### Priority

When multiple rules match, priority determines evaluation order. Higher
priority rules are evaluated first. However, because deny-overrides is
absolute, priority primarily affects which DENY rule is reported in the
evaluation result — any DENY at any priority wins.

---

## Postgres Row-Level Security

Postgres RLS provides database-level isolation as a defense-in-depth layer.
Even if application code has a bug that bypasses `ScopedManager`, the database
itself enforces that principals can only access their own rows.

### Enabling RLS

RLS requires two steps:

1. **Enable the flag on the backend:**

```python
from scoped.storage.postgres import PostgresBackend

backend = PostgresBackend(
    "postgresql://user:pass@host/db",
    enable_rls=True,
)
backend.initialize()
```

2. **Run migration m0013:**

```python
from scoped.storage.migrations.runner import MigrationRunner

runner = MigrationRunner(backend)
runner.discover()
runner.apply_all()  # m0013 creates RLS policies on all relevant tables
```

### How it works

When `enable_rls=True`, every database operation sets a Postgres session
variable before executing:

```sql
SET app.current_principal_id = 'alice-uuid-here';
```

RLS policies on each table reference this variable:

```sql
CREATE POLICY scoped_owner_isolation ON scoped_objects
    USING (owner_id = current_setting('app.current_principal_id', true));
```

The `true` parameter to `current_setting` means "return NULL if the setting
does not exist" rather than raising an error. This is critical — when no
principal is set (empty string), the policy evaluates to `owner_id = ''`,
which matches no rows. This is the **safe deny-all default**.

### SET LOCAL vs SET

The backend uses two different approaches depending on the context:

- **`SET LOCAL`** for explicit transactions. The variable is scoped to the
  transaction and automatically reset on commit or rollback. This is used in
  `PostgresBackend.transaction()`.

- **`SET`** (session-level) for autocommit operations. `SET LOCAL` is a no-op
  in autocommit mode, so the backend uses session-level `SET` and explicitly
  calls `RESET app.current_principal_id` after each operation to prevent
  leaking context across pooled connections.

### FORCE ROW LEVEL SECURITY

Migration m0013 applies `FORCE ROW LEVEL SECURITY` on every protected table.
Without `FORCE`, RLS policies do not apply to the table owner (the Postgres
role that owns the table). `FORCE` ensures policies apply even when connected
as the table owner — critical for production deployments where the application
connects as the table owner.

### Protected tables

RLS policies are applied to all tables with `owner_id` columns:

`scoped_objects`, `scopes`, `secrets`, `secret_versions`, `environments`,
`environment_templates`, `stages`, `pipelines`, `deployment_targets`,
`contracts`, `blobs`, `search_index`, `templates`, `retention_policies`,
`glacial_archives`, `event_subscriptions`, `webhook_endpoints`,
`notification_rules`, `recurring_schedules`, `scheduled_actions`, `jobs`.

Additionally:
- `scope_memberships` — filtered by `principal_id`
- `scope_projections` — filtered by scope membership (subquery)
- `notifications` — filtered by `recipient_id`

Tables without RLS: `audit_trail` (append-only, system-managed),
`registry_entries` (system-managed), `object_versions` (access controlled via
parent object), `rule_bindings` (system-managed).

---

## Database-Per-Tenant

For the strongest isolation guarantee, use `TenantRouter` to route each tenant
to their own database. See the [Multi-Tenant Isolation](multi-tenant.md) guide
for full setup instructions.

```python
from scoped.storage.tenant_router import TenantRouter

router = TenantRouter(
    tenant_resolver=lambda principal_id: lookup_tenant(principal_id),
    backend_factory=lambda tenant_id: PostgresBackend(
        f"postgresql://host/{tenant_id}_db", enable_rls=True
    ),
)
```

---

## Audit Trail Integrity

The audit trail is a SHA-256 hash chain. Each `TraceEntry` contains the hash
of the previous entry, creating a tamper-evident sequence.

### Hash chain construction

```
Entry 1: hash = SHA-256(seq=1, actor, action, target, timestamp, previous_hash="", state)
Entry 2: hash = SHA-256(seq=2, actor, action, target, timestamp, previous_hash=Entry1.hash, state)
Entry 3: hash = SHA-256(seq=3, actor, action, target, timestamp, previous_hash=Entry2.hash, state)
```

### Verification

`verify_chain()` walks the chain from `from_sequence` to `to_sequence`,
recomputing each hash and comparing it to the stored value:

```python
verification = scoped.audit.verify()

if not verification.valid:
    print(f"Chain broken at sequence {verification.break_at}")
```

### Chunked verification

For audit trails with millions of entries, verify in chunks:

```python
chunk_size = 10_000
seq = 1
while True:
    result = scoped.audit.verify(
        from_sequence=seq,
        to_sequence=seq + chunk_size - 1,
    )
    if not result.valid:
        print(f"Integrity violation at sequence {result.break_at}")
        break
    if result.last_seq < seq + chunk_size - 1:
        break  # Reached end of trail
    seq += chunk_size
```

### Thread safety

The `AuditWriter` uses a `threading.Lock` to protect sequence numbering and
hash chaining. In multi-process deployments (e.g. gunicorn workers sharing a
Postgres database), the writer re-seeds from the database under the lock to
prevent sequence collisions.

---

## Secret Handling

Secrets require special treatment because their values must never be exposed
through side channels.

### Encryption

Secret values are encrypted using **Fernet** (from the `cryptography`
library), which provides:

- **AES-128-CBC** for confidentiality
- **HMAC-SHA256** for authentication
- **Timestamp** in the token for rotation tracking

The encryption backend generates and manages keys. The default
`InMemoryBackend` generates a Fernet key at initialization. For production,
use a key management service (KMS) backend.

### Ref tokens

Secrets are never accessed directly. Instead, the owner grants a **ref token**
to a principal:

```python
with scoped.as_principal(admin):
    secret, sv = scoped.secrets.create("db-password", "s3cret-val")
    ref = scoped.secrets.grant_ref(secret.id, app_service)
```

The ref token is a capability — it encodes the secret ID and the grantee. To
access the secret value, the holder resolves the ref:

```python
with scoped.as_principal(app_service):
    plaintext = scoped.secrets.resolve(ref.token)
```

Resolution checks:
1. The ref is valid and not expired.
2. The accessor matches the ref's grantee.
3. The accessor is authorized (scope membership, rules).
4. An audit entry is recorded for the access.

### What never appears in audit state

The audit trail records that a secret was created, rotated, or accessed — but
the `before_state` and `after_state` fields in `TraceEntry` never contain the
plaintext value. The secret value is excluded at the `AuditWriter` level.
Similarly, environment snapshots, connector traffic, and export packages
exclude secret values.

### Leak detection

The compliance engine includes checks that scan audit entries, environment
state, and connector payloads for patterns that look like secret values. If a
value that matches a known secret ref pattern appears in an unexpected
location, the compliance check flags it.

---

## SQL Injection Prevention

pyscoped uses **parameterized queries throughout**. No SQL string is ever
constructed by concatenating user input.

### Query parameters

All storage backend methods accept `params` as tuples or dicts:

```python
backend.fetch_all(
    "SELECT * FROM scoped_objects WHERE owner_id = ? AND object_type = ?",
    (principal_id, object_type),
)
```

The `translate_placeholders` utility converts `?` placeholders to `%s` for
Postgres, ensuring the same queries work across SQLite and PostgreSQL.

### Column allowlists for ORDER BY

`ORDER BY` clauses cannot be parameterized in SQL. pyscoped validates column
names against explicit allowlists before including them in queries:

```python
# In the namespace API, order_by supports a fixed set of columns:
# "created_at", "-created_at", "object_type", "sequence", "timestamp", etc.
# Unknown column names raise ValueError.
scoped.objects.list(order_by="-created_at")  # Valid
scoped.objects.list(order_by="DROP TABLE")   # Raises ValueError
```

---

## Thread Safety

pyscoped is designed for use in multi-threaded web servers (gunicorn, uvicorn)
and concurrent applications. Key thread-safety mechanisms:

### Global client lock

The module-level `scoped.init()` function uses a `threading.Lock` to protect
initialization of the global client singleton. Multiple threads calling
`init()` concurrently will not create duplicate clients.

### Audit writer mutex

The `AuditWriter` uses a `threading.Lock` to protect:
- Sequence number increment
- Hash chain computation (reading previous hash, computing new hash, writing)
- Database write of the new entry

This ensures that even under concurrent writes, the hash chain remains
consistent and sequence numbers are never duplicated.

### TenantRouter double-checked locking

The `TenantRouter` uses a double-checked locking pattern for its backend
cache:

```python
def _get_backend(self, tenant_id: str) -> StorageBackend:
    # Fast path — no lock
    backend = self._backends.get(tenant_id)
    if backend is not None:
        return backend

    # Slow path — acquire lock, check again
    with self._lock:
        backend = self._backends.get(tenant_id)
        if backend is not None:
            return backend
        backend = self._factory(tenant_id)
        backend.initialize()
        self._backends[tenant_id] = backend
        return backend
```

This ensures that only one thread creates and initializes a backend for any
given tenant, while subsequent requests take the fast path without locking.

### ScopedContext via contextvars

`ScopedContext` is implemented with Python's `contextvars` module, which is
inherently thread-safe (each thread gets its own context) and async-safe (each
async task inherits a copy of the context). There is no global mutable state
in the context system.

---

## Cryptographic Details

### Audit trail: SHA-256

The audit hash chain uses SHA-256 (via Python's `hashlib`). Each entry's hash
is computed over the canonical serialization of: sequence, actor_id, action,
target_type, target_id, timestamp, previous_hash, before_state, and
after_state.

- **Algorithm:** SHA-256 (256-bit digest)
- **Purpose:** Tamper detection, not encryption
- **Chain property:** Each hash incorporates the previous hash, creating an
  append-only structure where any modification invalidates all subsequent
  entries

### Secrets: Fernet (AES-128-CBC + HMAC-SHA256)

Secret values are encrypted using Fernet, which combines:

- **AES-128-CBC** for encryption (128-bit key, random IV per token)
- **HMAC-SHA256** for authentication (128-bit signing key)
- **Timestamp** embedded in each token

Fernet guarantees that ciphertext cannot be modified or forged without the key.
The combined key is 256 bits (128 for AES + 128 for HMAC).

### Management plane sync: HMAC-SHA256

The sync agent signs payloads sent to the management plane using HMAC-SHA256,
keyed with a derivative of the API key. This ensures:

- **Authenticity:** The management plane can verify the payload came from a
  legitimate SDK instance.
- **Integrity:** The payload was not modified in transit.
- **Non-repudiation:** Only the holder of the API key could have produced the
  signature.

---

## Security Checklist

When deploying pyscoped in production:

1. **Use PostgreSQL** with connection pooling (`pyscoped[postgres]`).
2. **Enable RLS** (`enable_rls=True`) and run migration m0013.
3. **Use database-per-tenant** for hard isolation between tenants.
4. **Set up the compliance engine** to validate invariants in CI.
5. **Verify the audit chain** regularly (daily cron or continuous).
6. **Rotate secrets** on a schedule using `scoped.secrets.rotate()`.
7. **Set `SCOPED_LOG_LEVEL`** to control what appears in logs.
8. **Monitor sync status** if using the management plane.
9. **Restrict direct database access** — all operations should go through
   pyscoped's API to ensure isolation and audit.
10. **Test with `IsolationFuzzer`** to catch edge cases in access patterns.
