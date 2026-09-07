# Layer 6: Audit & Trace

## Purpose

The audit layer is the system of record. Its rule is absolute: **if it didn't produce a trace, it didn't happen.**

Every action across every layer — creates, reads, updates, deletes, shares, revocations, rule evaluations, logins, environment spawns, connector syncs, secret accesses — produces an immutable trace entry. The audit trail is append-only and hash-chained for tamper detection.

This is not logging. This is the compliance backbone. The audit trail is what makes everything else provable.

## Core Concepts

### TraceEntry

A single record of something that happened.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `sequence` | Monotonically increasing number — total ordering of all events |
| `actor_id` | The principal who performed the action |
| `action` | What happened (ActionType enum — 44 distinct types) |
| `target_type` | What kind of thing was acted on |
| `target_id` | Which specific thing |
| `scope_id` | The scope context (if applicable) |
| `timestamp` | When it happened |
| `before_state` | JSON of the target's state before the action (null for creates) |
| `after_state` | JSON of the target's state after the action (null for deletes) |
| `metadata_json` | Additional context (IP, user agent, environment, etc.) |
| `parent_trace_id` | For nested operations — links to the parent trace |
| `hash` | SHA-256 hash of this entry |
| `previous_hash` | Hash of the previous entry (chain link) |

### Hash Chain

Each trace entry includes the hash of the previous entry. This creates a chain:

```
Entry 1: hash=H(data1), previous_hash=""
Entry 2: hash=H(data2 + H1), previous_hash=H1
Entry 3: hash=H(data3 + H2), previous_hash=H2
...
```

To verify integrity: walk the chain from any point and confirm that each entry's `previous_hash` matches the `hash` of the preceding entry. If anyone modifies a historical entry, the chain breaks.

### Nested Traces

Complex operations produce multiple trace entries. A "promote object from environment to scope" action produces:
1. Parent trace: PROMOTION (the top-level action)
2. Child trace: PROJECTION (the object being projected into the scope)
3. Child trace: SCOPE_MODIFY (the scope gaining a new projection)
4. Child trace: ENV_COMPLETE (if the environment is completed after promotion)

Each child references the parent via `parent_trace_id`. This gives a tree of causally related events.

### Visibility Filtering

Not everyone can see all traces. Visibility is governed by rules (Layer 5):
- By default, principals can see traces for actions they performed
- Scope admins can see traces for actions within their scope
- Visibility rules can broaden or narrow this

This is critical: the audit trail itself is scoped. An employee shouldn't see traces from a scope they were never part of, even if those traces exist in the same database.

### What Gets Traced (Everything)

| Layer | Actions Traced |
|-------|---------------|
| Registry | register, unregister, lifecycle_change |
| Identity | principal create/update, relationship changes |
| Objects | create, read, update, delete (tombstone) |
| Tenancy | scope create/modify/dissolve, membership changes, projections |
| Rules | rule create/update, binding changes, every rule evaluation |
| Audit | (self-referential: audit queries are traced) |
| Temporal | rollback operations |
| Environments | spawn, suspend, resume, complete, discard, promote, snapshot |
| Flow | stage transitions, flow pushes, promotions |
| Deployments | deploy, rollback, gate checks |
| Secrets | create, read (not the value), rotate, revoke, ref grant, ref resolve |
| Integrations | connect, disconnect, plugin lifecycle, hook executions |
| Connector | propose, approve, revoke, sync, marketplace publish/install |

**Secret values are NEVER included in `before_state` or `after_state`.** Secret traces log that access happened, not what the value was.

## How It Connects

### To Every Other Layer
Audit is consumed by every layer and consumes from every layer. It is the universal observer.

### To Layer 5 (Rules)
Visibility rules govern who can query the audit trail. Rule evaluations themselves produce trace entries.

### To Layer 7 (Temporal)
The audit trail is what makes temporal reconstruction possible. "What was the state of object X at time T?" is answered by querying the audit trail for the most recent trace entry targeting X before T and reading its `after_state`.

### To Layer 0 (Compliance)
The compliance engine validates that every code path produces traces. If an action completes without a corresponding trace entry, `ComplianceViolation` is raised.

## Files

```
scoped/audit/
    __init__.py
    models.py        # TraceEntry, TraceChain
    writer.py        # Append-only writer, hash chaining, batch flush
    query.py         # Query traces with rule-based visibility filtering
    middleware.py     # Django middleware that wraps every request in a trace context
```

## Schema

```sql
CREATE TABLE audit_trail (
    id              TEXT PRIMARY KEY,
    sequence        INTEGER NOT NULL,
    actor_id        TEXT NOT NULL,
    action          TEXT NOT NULL,
    target_type     TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    scope_id        TEXT,
    timestamp       TEXT NOT NULL,
    before_state    TEXT,
    after_state     TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    parent_trace_id TEXT REFERENCES audit_trail(id),
    hash            TEXT NOT NULL,
    previous_hash   TEXT NOT NULL DEFAULT ''
);
```

## Invariants

1. Every action produces a trace entry. No exceptions.
2. The audit trail is append-only. Entries are never modified or deleted.
3. Hash chain integrity is verifiable at any time.
4. Secret values never appear in trace states.
5. Audit visibility is governed by rules.
6. Trace sequence numbers are monotonically increasing and gap-free.
