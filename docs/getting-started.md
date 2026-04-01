---
title: Getting Started
description: Quickstart guide for pyscoped — from installation to audit trail verification in under ten minutes.
category: guides
---

# Getting Started

This guide takes you from zero to a working pyscoped application. By the end
you will have principals, versioned objects, scope-based sharing, a
tamper-evident audit trail, and chain verification running — all in about fifty
lines of code.

---

## 1. Installation

pyscoped is published on PyPI and imported as `scoped`. The core package has
zero runtime dependencies — SQLite ships with Python.

```bash
pip install pyscoped
```

**Requirements:** Python 3.11+ (developed and tested on 3.13).

### Extras

Install optional extras for production backends and framework integrations:

```bash
# PostgreSQL (psycopg v3 + connection pool)
pip install pyscoped[postgres]

# Web framework adapters
pip install pyscoped[django]
pip install pyscoped[fastapi]
pip install pyscoped[flask]

# OpenTelemetry instrumentation
pip install pyscoped[otel]
```

You can combine extras:

```bash
pip install pyscoped[postgres,fastapi,otel]
```

Verify the install:

```python
import scoped
print(scoped.__version__)  # e.g. "0.1.1"
```

---

## 2. Zero-Config Start (In-Memory SQLite)

The fastest way to start is with no arguments at all. `scoped.init()` creates
an in-memory SQLite database, initializes the full 16-layer schema, and
returns a `ScopedClient`:

```python
import scoped

client = scoped.init()
```

That single line gives you the complete framework — principals, versioned
objects, scopes, rules, audit, secrets, and more. The in-memory backend is
ideal for development, testing, and experimentation. Everything disappears when
the process exits.

For file-backed persistence during development:

```python
client = scoped.init(database_url="sqlite:///app.db")
```

---

## 3. Creating Principals

A **principal** is any entity that acts in the system: a user, a team, a
service account, an AI agent. The `kind` field is application-defined — pyscoped
does not prescribe what kinds exist.

```python
import scoped

client = scoped.init()

alice = scoped.principals.create("Alice", kind="user")
bob = scoped.principals.create("Bob", kind="user")
ops_bot = scoped.principals.create("DeployBot", kind="service")

print(alice.id)            # UUID hex string, e.g. "a1b2c3d4..."
print(alice.kind)          # "user"
print(alice.display_name)  # "Alice"
```

Every principal is automatically registered in the universal registry (Layer 1)
and receives a unique URN. Principals persist for the lifetime of the database.

### Listing and finding principals

```python
users = scoped.principals.list(kind="user")        # All users
found = scoped.principals.get(alice.id)             # By ID (raises if missing)
maybe = scoped.principals.find(alice.id)            # By ID (returns None if missing)
```

---

## 4. Setting the Acting Principal

Every operation in pyscoped requires an acting principal. Use `as_principal()`
to declare who is performing the work:

```python
with scoped.as_principal(alice):
    # Everything in this block is attributed to Alice.
    # The context is thread-safe (via contextvars) and async-safe.
    doc, v1 = scoped.objects.create("invoice", data={"amount": 500})
```

Without an active principal context, operations raise `NoContextError`.

Contexts nest — entering a new context pushes a frame that is restored on exit:

```python
with scoped.as_principal(alice):
    # Alice is acting
    with scoped.as_principal(bob):
        # Bob is acting (Alice's context is saved)
        pass
    # Alice is acting again
```

---

## 5. Creating and Updating Objects

A **ScopedObject** is a versioned, isolated data record. Every mutation creates
a new immutable version — nothing is modified in place.

### Create

```python
with scoped.as_principal(alice):
    doc, v1 = scoped.objects.create(
        "document",
        data={"title": "Q4 Report", "status": "draft"},
    )

    print(doc.id)              # Unique object ID
    print(doc.object_type)     # "document"
    print(doc.owner_id)        # alice.id
    print(doc.current_version) # 1
    print(v1.data)             # {"title": "Q4 Report", "status": "draft"}
```

Objects are **creator-private by default**. Only Alice can see this document.
Bob gets `None`:

```python
with scoped.as_principal(bob):
    result = scoped.objects.get(doc.id)
    print(result)  # None — Bob cannot see Alice's object
```

### Update (creates a new version)

```python
with scoped.as_principal(alice):
    doc, v2 = scoped.objects.update(
        doc.id,
        data={"title": "Q4 Report", "status": "final"},
        change_reason="Finalized for review",
    )

    print(doc.current_version)  # 2
    print(v2.data["status"])    # "final"
```

### Soft delete (tombstone)

Nothing is truly deleted in pyscoped. Objects are tombstoned — the record and
all versions remain for audit and rollback:

```python
with scoped.as_principal(alice):
    scoped.objects.delete(doc.id, reason="Superseded by Q1 report")
```

### Version history

```python
with scoped.as_principal(alice):
    versions = scoped.objects.versions(doc.id)
    for v in versions:
        print(f"v{v.version}: {v.data} ({v.change_reason})")
```

---

## 6. Creating Scopes and Sharing via Projection

A **scope** is the sharing primitive — a named isolation boundary. You create a
scope, add members with roles, and project objects into it.

### Create a scope

```python
with scoped.as_principal(alice):
    team = scoped.scopes.create("Engineering", description="Core team workspace")
    # Alice is automatically added as the OWNER member.
```

### Add members

```python
with scoped.as_principal(alice):
    scoped.scopes.add_member(team, bob, role="editor")
```

Available roles: `"viewer"`, `"editor"`, `"admin"`, `"owner"`.

### Project an object (share it)

Only the object's **owner** can project it. This is the explicit sharing act:

```python
with scoped.as_principal(alice):
    doc, _ = scoped.objects.create("design_doc", data={"title": "Architecture v2"})

    # Project the document into the team scope
    scoped.scopes.project(doc, team, access_level="read")
    # Bob (an editor in the team scope) can now see this document.
```

### Revoke a projection

```python
with scoped.as_principal(alice):
    scoped.scopes.unproject(doc, team)
    # Bob can no longer see the document. Revocation is immediate.
```

### Batch member operations

```python
with scoped.as_principal(alice):
    scoped.scopes.add_members(team, [
        (charlie, "viewer"),
        (dana, "editor"),
        (ops_bot, "viewer"),
    ])
```

---

## 7. Querying the Audit Trail

Every action in pyscoped produces an immutable, hash-chained trace entry. The
chain links each entry to its predecessor via a SHA-256 hash, making tampering
detectable.

```python
with scoped.as_principal(alice):
    # Full audit history for a specific object
    trail = scoped.audit.for_object(doc.id)
    for entry in trail:
        print(f"[{entry.sequence}] {entry.action.value} by {entry.actor_id} at {entry.timestamp}")

    # History for a principal
    alice_trail = scoped.audit.for_principal(alice.id, limit=50)

    # Flexible querying with filters
    from scoped.types import ActionType
    creates = scoped.audit.query(
        actor_id=alice.id,
        action=ActionType.CREATE,
        since=yesterday,
        limit=100,
    )
```

---

## 8. Verifying Chain Integrity

The audit trail is a SHA-256 hash chain. Each entry contains the hash of the
previous entry, creating a tamper-evident sequence. If any entry is modified or
deleted, the chain breaks:

```python
verification = scoped.audit.verify()

print(verification.valid)       # True if chain is intact
print(verification.entries)     # Number of entries verified
print(verification.first_seq)   # First sequence number
print(verification.last_seq)    # Last sequence number
```

For large audit trails, verify specific ranges:

```python
# Verify only entries in a range
partial = scoped.audit.verify(from_sequence=1000, to_sequence=2000)
```

---

## 9. PostgreSQL Setup

For production, use PostgreSQL with connection pooling:

```python
import scoped

client = scoped.init(
    database_url="postgresql://user:password@localhost:5432/myapp",
)
```

This creates a PostgresBackend with psycopg v3 and a managed connection pool.
The full schema is created automatically on first init.

### Connection pool tuning

For advanced control, construct the backend directly:

```python
from scoped.storage.postgres import PostgresBackend

backend = PostgresBackend(
    "postgresql://user:password@localhost:5432/myapp",
    pool_min_size=5,
    pool_max_size=20,
    pool_timeout=30.0,
    enable_rls=True,   # Row-level security (see Security docs)
)
backend.initialize()

client = scoped.ScopedClient(backend=backend)
```

---

## 10. Management Plane Sync

The pyscoped management plane provides a dashboard, compliance reports, and
alerting. Connect your SDK instance with an API key:

```python
import scoped

client = scoped.init(
    database_url="postgresql://user:pass@host/db",
    api_key="psc_live_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
)

# Start pushing audit metadata to the management plane
client.start_sync()
```

API key formats:
- `psc_live_<32hex>` for production
- `psc_test_<32hex>` for sandbox/staging

The SDK works fully without an API key and without sync enabled. The management
plane is optional and additive — it does not change SDK behavior.

Check sync status:

```python
status = client.sync_status()
print(status)
```

---

## 11. Complete Example

Here is a self-contained script that demonstrates every concept from this guide:

```python
import scoped

# --- Initialize (in-memory SQLite, zero config) ---
client = scoped.init()

# --- Create principals ---
alice = scoped.principals.create("Alice", kind="user")
bob = scoped.principals.create("Bob", kind="user")

with scoped.as_principal(alice):
    # --- Create an object (creator-private by default) ---
    doc, v1 = scoped.objects.create(
        "design_doc",
        data={"title": "Architecture v2", "status": "draft"},
    )

    # --- Verify isolation: Bob cannot see Alice's object ---
    with scoped.as_principal(bob):
        assert scoped.objects.get(doc.id) is None

    # --- Update creates a new version ---
    doc, v2 = scoped.objects.update(
        doc.id,
        data={"title": "Architecture v2", "status": "review"},
        change_reason="Ready for team review",
    )
    assert doc.current_version == 2

    # --- Create a scope and share ---
    team = scoped.scopes.create("Engineering")
    scoped.scopes.add_member(team, bob, role="editor")
    scoped.scopes.project(doc, team, access_level="read")

    # --- Query the audit trail ---
    trail = scoped.audit.for_object(doc.id)
    print(f"Audit trail ({len(trail)} entries):")
    for entry in trail:
        print(f"  [{entry.sequence}] {entry.action.value} -> "
              f"{entry.target_type}:{entry.target_id}")

    # --- Verify chain integrity ---
    verification = scoped.audit.verify()
    print(f"\nChain integrity: valid={verification.valid}, "
          f"entries={verification.entries}")

print("\nDone.")
```

---

## 12. Next Steps

Now that you have the fundamentals:

- **[Architecture](architecture.md)** — the 16-layer model, invariants, and
  design philosophy.

- **[Security & Isolation](security.md)** — defense-in-depth isolation, rules
  engine, Postgres RLS, secret handling, and cryptographic details.

- **[Multi-Tenant Isolation](multi-tenant.md)** — Postgres RLS,
  database-per-tenant routing, and combining both tiers.

- **[API Reference](api-reference.md)** — complete namespace and model
  documentation.

- **Layer documentation** — detailed docs for each of the 16 layers in
  [`docs/layers/`](layers/).

- **Extensions** — schema migrations, contracts, blobs, search, templates,
  tiering, and import/export in [`docs/extensions/`](extensions/).

- **Framework adapters** — integrate with Django, FastAPI, Flask, or MCP:

  ```bash
  pip install pyscoped[django]
  pip install pyscoped[fastapi]
  pip install pyscoped[flask]
  ```

---

## The 10 Invariants

These are the guarantees pyscoped enforces at every layer. They are absolute
and cannot be overridden:

1. **Nothing exists without registration.** Every construct has a registry entry with a URN.
2. **Nothing happens without identity.** Every operation requires a principal.
3. **Nothing is shared by default.** Every object starts creator-private.
4. **Nothing happens without a trace.** Every action produces a hash-chained audit entry.
5. **Nothing is truly deleted.** Objects are tombstoned; versions are retained.
6. **Deny always wins.** DENY rules override ALLOW rules.
7. **Revocation is immediate.** Same-transaction enforcement, not eventual consistency.
8. **Everything is versioned.** Every mutation creates a new immutable version.
9. **Everything is rollbackable.** Any action can be reversed to any point in time.
10. **Secrets never leak.** Values never appear in audit trails, snapshots, or connector traffic.
