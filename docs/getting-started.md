# Getting Started with Scoped

This guide walks you through building a real application on Scoped -- from
installing the package to rolling back changes across time.  Every code example
is runnable.  By the end you will have principals, versioned objects, scoped
sharing, policy rules, an auditable hash-chained trail, and point-in-time
rollback.

---

## 1. Installation

Scoped is published on PyPI as **`pyscoped`** and imported as **`scoped`**.  It
has zero runtime dependencies -- SQLite ships with Python.

```bash
pip install pyscoped
```

Requirements: **Python 3.11+** (developed and tested on 3.13).

Verify the install:

```python
import scoped
print(scoped.__version__)  # e.g. "0.1.1"
```

---

## 2. Initialize Storage

Everything in Scoped is persisted through a `StorageBackend`.  The built-in
`SQLiteBackend` works with both on-disk files and in-memory databases.

```python
from scoped.storage.sqlite import SQLiteBackend

# Use ":memory:" for experiments, or a file path for persistence.
backend = SQLiteBackend(":memory:")
backend.initialize()  # Creates all tables -- call this exactly once.
```

> **Important:** Always call `backend.initialize()` after creating the backend.
> It sets up the full schema (registry, principals, objects, scopes, audit
> trail, rules, and more).

For a real application, point at a file:

```python
backend = SQLiteBackend("my_app.db")
backend.initialize()
```

---

## 3. Create Principals

A **principal** is any entity that can act in the system -- a user, a bot, a
service account, a team.  The `kind` field is application-defined; Scoped does
not prescribe what kinds exist.

```python
from scoped.identity.principal import PrincipalStore

principals = PrincipalStore(backend)

alice = principals.create_principal(kind="user", display_name="Alice")
bob = principals.create_principal(kind="user", display_name="Bob")

print(alice.id)            # UUID hex string, e.g. "a1b2c3d4..."
print(alice.kind)          # "user"
print(alice.display_name)  # "Alice"
```

Every principal is automatically registered in the universal registry (Layer 1)
and receives a unique URN.

> **Common pitfall:** The method is `create_principal()`, not `create()`.

### ScopedContext -- who is acting

Every operation in Scoped requires an acting principal.  Wrap your work in a
`ScopedContext`:

```python
from scoped.identity.context import ScopedContext

with ScopedContext(principal=alice):
    ctx = ScopedContext.current()
    print(ctx.principal_id)    # alice.id
    print(ctx.principal_kind)  # "user"
```

Contexts nest -- entering a new context pushes a frame that is restored on exit.

---

## 4. Create Objects

A **ScopedObject** is a versioned, isolated data record.  Every mutation
creates a new version; nothing is modified in place.

```python
from scoped.objects.manager import ScopedManager
from scoped.audit.writer import AuditWriter
from scoped.types import ActionType

# Wire up the audit writer so every object operation is traced automatically.
audit = AuditWriter(backend)
manager = ScopedManager(backend, audit_writer=audit)

with ScopedContext(principal=alice):
    # Create -- returns (object, version_1)
    doc, v1 = manager.create(
        object_type="document",
        owner_id=alice.id,
        data={"title": "Draft", "body": "Hello, world."},
    )

    print(doc.id)              # unique object ID
    print(doc.object_type)     # "document"
    print(doc.owner_id)        # alice.id
    print(doc.current_version) # 1
    print(v1.data)             # {"title": "Draft", "body": "Hello, world."}
```

### Read

```python
    # Owner can always read their own objects.
    fetched = manager.get(doc.id, principal_id=alice.id)
    print(fetched.id == doc.id)  # True
```

### Update (creates a new version)

```python
    doc, v2 = manager.update(
        doc.id,
        principal_id=alice.id,
        data={"title": "Final", "body": "Hello, world."},
        change_reason="Finalized title",
    )

    print(doc.current_version)  # 2
    print(v2.data["title"])     # "Final"
```

### Soft delete (tombstone)

Nothing is truly deleted in Scoped.  Objects are tombstoned -- the record and
all versions remain.

```python
    tombstone = manager.tombstone(doc.id, principal_id=alice.id, reason="obsolete")
    print(tombstone.reason)  # "obsolete"
```

---

## 5. Understand Isolation

Scoped enforces **creator-private by default**.  When Alice creates an object,
only Alice can see it.  Bob gets `None`:

```python
with ScopedContext(principal=alice):
    doc, _ = manager.create(
        object_type="note",
        owner_id=alice.id,
        data={"text": "Private thought"},
    )

with ScopedContext(principal=bob):
    result = manager.get(doc.id, principal_id=bob.id)
    print(result)  # None -- Bob cannot see Alice's object
```

This is **Invariant 3: Nothing is shared by default.**  Sharing must be
explicit, and it happens through scopes and projections (next section).

> **Note:** `ScopedManager.get()` enforces owner-only access.  Scope projections
> enable visibility through a separate query path in the tenancy engine, not
> through the manager.

---

## 6. Share via Scopes

A **scope** is a named isolation boundary -- the sharing primitive.  You create
a scope, add members, and project objects into it.

### Create a scope

```python
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.tenancy.projection import ProjectionManager
from scoped.tenancy.models import ScopeRole, AccessLevel

scopes = ScopeLifecycle(backend, audit_writer=audit)
projections = ProjectionManager(backend, audit_writer=audit)

with ScopedContext(principal=alice):
    # Create a scope -- the owner is automatically added as OWNER member.
    team = scopes.create_scope(name="Team Alpha", owner_id=alice.id)

    print(team.id)    # unique scope ID
    print(team.name)  # "Team Alpha"
```

> **Common pitfall:** The method is `create_scope()`, not `create()`.

### Add members

```python
    # Add Bob as an EDITOR.  ScopeRole is an enum, not a string.
    scopes.add_member(
        team.id,
        principal_id=bob.id,
        role=ScopeRole.EDITOR,
        granted_by=alice.id,
    )
```

Available roles: `ScopeRole.VIEWER`, `ScopeRole.EDITOR`, `ScopeRole.ADMIN`,
`ScopeRole.OWNER`.

> **Common pitfall:** `add_member()` requires `granted_by` and the `ScopeRole`
> enum -- not a string.

### Project an object into a scope

Only the object's **owner** can project it.  This is the explicit sharing act.

```python
    # First, create a document to share.
    doc, _ = manager.create(
        object_type="document",
        owner_id=alice.id,
        data={"title": "Shared Design Doc"},
    )

    # Project the document into the team scope.
    projection = projections.project(
        scope_id=team.id,
        object_id=doc.id,
        projected_by=alice.id,
        access_level=AccessLevel.READ,  # or AccessLevel.WRITE, AccessLevel.ADMIN
    )

    print(projection.scope_id)   # team.id
    print(projection.object_id)  # doc.id
```

### Revoke a projection

```python
    projections.revoke_projection(
        scope_id=team.id,
        object_id=doc.id,
        revoked_by=alice.id,
    )
```

> **Common pitfall:** The method is `revoke_projection()`, not `revoke()`.

Revocation is immediate (Invariant 7) -- it takes effect in the same
transaction, not eventually.

---

## 7. Enforce Rules

The rule engine uses a **deny-overrides** model: when rules conflict, DENY
always wins (Invariant 6).

### Create a rule

```python
from scoped.rules.engine import RuleStore, RuleEngine
from scoped.rules.models import RuleType, RuleEffect, BindingTargetType

rule_store = RuleStore(backend, audit_writer=audit)

with ScopedContext(principal=alice):
    # Create a DENY rule for external access.
    deny_rule = rule_store.create_rule(
        name="deny-external-read",
        rule_type=RuleType.ACCESS,
        effect=RuleEffect.DENY,
        conditions={"action": ["read"], "principal_kind": ["external"]},
        priority=10,
        created_by=alice.id,
    )
```

### Bind the rule to a scope

Rules are inert until bound to a target -- a scope, a principal, an object
type, etc.

```python
    rule_store.bind_rule(
        deny_rule.id,
        target_type=BindingTargetType.SCOPE,
        target_id=team.id,
        bound_by=alice.id,
    )
```

### Evaluate access

```python
    engine = RuleEngine(backend)

    result = engine.evaluate(
        action="read",
        principal_id=bob.id,
        scope_id=team.id,
    )

    print(result.allowed)       # True or False
    print(result.deny_rules)    # tuple of matching DENY rules
    print(result.allow_rules)   # tuple of matching ALLOW rules
```

The `EvaluationResult` is also truthy/falsy:

```python
    if engine.evaluate(action="write", principal_id=bob.id, scope_id=team.id):
        print("Access granted")
    else:
        print("Access denied")
```

---

## 8. Audit Trail

Every action in Scoped produces an immutable, hash-chained trace entry
(Invariant 4).  The chain links each entry to its predecessor via a SHA-256
hash, making tampering detectable.

### Record a trace entry

If you passed `audit_writer` to `ScopedManager`, `ScopeLifecycle`, or
`RuleStore`, traces are recorded automatically.  You can also record custom
entries:

```python
from scoped.audit.writer import AuditWriter
from scoped.audit.query import AuditQuery
from scoped.types import ActionType

# The audit writer you created earlier.
audit = AuditWriter(backend)

entry = audit.record(
    actor_id=alice.id,
    action=ActionType.CREATE,
    target_type="report",
    target_id="report-001",
    after_state={"title": "Q4 Results"},
)

print(entry.id)        # unique trace entry ID
print(entry.sequence)  # monotonically increasing integer
print(entry.hash)      # SHA-256 hash linking to the previous entry
```

### Query the trail

```python
query = AuditQuery(backend)

# Full history for a specific target.
history = query.history(target_type="report", target_id="report-001")
for e in history:
    print(f"[{e.sequence}] {e.action.value} by {e.actor_id} at {e.timestamp}")

# Flexible filtering.
entries = query.query(
    actor_id=alice.id,
    action=ActionType.CREATE,
    limit=50,
)
```

### Verify chain integrity

```python
verification = query.verify_chain()
print(verification)  # Shows whether the hash chain is intact
```

If any entry has been tampered with, the verification will flag the break.

---

## 9. Rollback

Every action can be reversed to any point in time (Invariant 9).  Rollbacks are
themselves traced -- you get full auditability of the undo itself.

### Setup

```python
from scoped.temporal.rollback import RollbackExecutor

rollback = RollbackExecutor(backend, audit_writer=audit)
```

### Roll back a single action

Pass the `trace_id` of the audit entry you want to reverse:

```python
with ScopedContext(principal=alice):
    # Create an object and capture the trace entry.
    doc, _ = manager.create(
        object_type="memo",
        owner_id=alice.id,
        data={"text": "This was a mistake."},
    )

    # Find the trace entry for the create action.
    query = AuditQuery(backend)
    history = query.history(target_type="memo", target_id=doc.id)
    create_trace = history[0]

    # Roll it back.
    result = rollback.rollback_action(
        create_trace.id,
        actor_id=alice.id,
        reason="Created in error",
    )

    print(result.success)         # True
    print(result.rolled_back)     # (trace_id,)
    print(result.rollback_trace_ids)  # IDs of the new rollback trace entries
```

### Roll back to a point in time

Restore a target to its state at a specific timestamp.  All actions after that
timestamp are reversed in reverse chronological order:

```python
from datetime import datetime, timezone

# Snapshot a timestamp before a series of changes.
checkpoint = datetime.now(timezone.utc)

# ... make several updates ...
manager.update(doc.id, principal_id=alice.id, data={"text": "Change 1"})
manager.update(doc.id, principal_id=alice.id, data={"text": "Change 2"})
manager.update(doc.id, principal_id=alice.id, data={"text": "Change 3"})

# Roll everything back to the checkpoint.
result = rollback.rollback_to_timestamp(
    target_type="memo",
    target_id=doc.id,
    at=checkpoint,
    actor_id=alice.id,
    reason="Reverting to last known good state",
)

print(f"Rolled back {len(result.rolled_back)} actions")
```

---

## 10. Putting It All Together

Here is a complete, runnable script that uses everything covered above:

```python
from scoped.storage.sqlite import SQLiteBackend
from scoped.identity.principal import PrincipalStore
from scoped.identity.context import ScopedContext
from scoped.objects.manager import ScopedManager
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.tenancy.projection import ProjectionManager
from scoped.tenancy.models import ScopeRole, AccessLevel
from scoped.audit.writer import AuditWriter
from scoped.audit.query import AuditQuery
from scoped.rules.engine import RuleStore, RuleEngine
from scoped.rules.models import RuleType, RuleEffect, BindingTargetType
from scoped.types import ActionType

# --- Bootstrap ---
backend = SQLiteBackend(":memory:")
backend.initialize()

audit = AuditWriter(backend)
principals = PrincipalStore(backend, audit_writer=audit)
manager = ScopedManager(backend, audit_writer=audit)
scopes = ScopeLifecycle(backend, audit_writer=audit)
projections = ProjectionManager(backend, audit_writer=audit)
rule_store = RuleStore(backend, audit_writer=audit)
rule_engine = RuleEngine(backend)
query = AuditQuery(backend)

# --- Create principals ---
alice = principals.create_principal(kind="user", display_name="Alice")
bob = principals.create_principal(kind="user", display_name="Bob")

with ScopedContext(principal=alice):
    # --- Create an object (creator-private) ---
    doc, v1 = manager.create(
        object_type="design_doc",
        owner_id=alice.id,
        data={"title": "Architecture v2", "status": "draft"},
    )

    # --- Verify isolation ---
    assert manager.get(doc.id, principal_id=bob.id) is None  # Bob cannot see it

    # --- Create a scope and share ---
    team = scopes.create_scope(name="Engineering", owner_id=alice.id)
    scopes.add_member(
        team.id,
        principal_id=bob.id,
        role=ScopeRole.EDITOR,
        granted_by=alice.id,
    )
    projections.project(
        scope_id=team.id,
        object_id=doc.id,
        projected_by=alice.id,
        access_level=AccessLevel.READ,
    )

    # --- Add a policy rule ---
    rule = rule_store.create_rule(
        name="allow-team-read",
        rule_type=RuleType.ACCESS,
        effect=RuleEffect.ALLOW,
        conditions={"action": ["read"]},
        priority=5,
        created_by=alice.id,
    )
    rule_store.bind_rule(
        rule.id,
        target_type=BindingTargetType.SCOPE,
        target_id=team.id,
        bound_by=alice.id,
    )

    # --- Evaluate access ---
    result = rule_engine.evaluate(
        action="read",
        principal_id=bob.id,
        scope_id=team.id,
    )
    print(f"Bob can read: {result.allowed}")

    # --- Inspect the audit trail ---
    trail = query.query(actor_id=alice.id, limit=20)
    print(f"\nAudit trail ({len(trail)} entries):")
    for entry in trail:
        print(f"  [{entry.sequence}] {entry.action.value} -> {entry.target_type}:{entry.target_id}")

    # --- Verify chain integrity ---
    verification = query.verify_chain()
    print(f"\nChain integrity: {verification}")

print("\nDone.")
```

---

## 11. Next Steps

Now that you have the fundamentals, explore deeper:

- **Layer documentation** -- detailed docs for each of the 16 layers live in
  [`docs/layers/`](layers/), from `00-compliance.md` through `16-scheduling.md`.

- **Extensions** -- 9 extensions enrich existing layers (migrations, contracts,
  blobs, config hierarchy, search, templates, tiering, import/export).  See
  [`docs/extensions/`](extensions/).

- **Architecture overview** -- the full system design is in
  [`docs/architecture.md`](architecture.md).

- **Framework adapters** -- integrate Scoped with Django, FastAPI, Flask, or
  MCP.  Install with extras:

  ```bash
  pip install pyscoped[django]
  pip install pyscoped[fastapi]
  pip install pyscoped[flask]
  pip install pyscoped[mcp]
  ```

  Adapter documentation lives in `scoped/contrib/`.

- **Advanced features** -- environments (Layer 8), flow pipelines (Layer 9),
  deployments with gate checks (Layer 10), encrypted secrets (Layer 11),
  sandboxed integrations (Layer 12), cross-org federation (Layer 13), event bus
  (Layer 14), notifications (Layer 15), and scheduled jobs (Layer 16).

---

## The 10 Invariants

These are the guarantees Scoped enforces at every layer.  They are absolute and
cannot be overridden:

1. **Nothing exists without registration.** Every construct has a registry entry with a URN.
2. **Nothing happens without identity.** Every operation requires a `ScopedContext` with an acting principal.
3. **Nothing is shared by default.** Every object starts creator-private.
4. **Nothing happens without a trace.** Every action produces an immutable, hash-chained audit entry.
5. **Nothing is truly deleted.** Objects are tombstoned. Versions are retained. Audit is append-only.
6. **Deny always wins.** When rules conflict, DENY overrides ALLOW.
7. **Revocation is immediate.** Same-transaction enforcement, not eventual consistency.
8. **Everything is versioned.** Every mutation creates a new version.
9. **Everything is rollbackable.** Any action can be reversed to any point in time.
10. **Secrets never leak.** Values never appear in audit trails, snapshots, or connector traffic.

These invariants are enforced by the compliance engine (Layer 0) and validated
by over 1,400 tests.
