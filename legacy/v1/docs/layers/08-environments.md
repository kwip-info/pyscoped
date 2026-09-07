# Layer 8: Environments

## Purpose

Environments are the **unit of work**. They're the collaborative equivalent of a throwaway AI conversation — an isolated workspace where a task happens, and when it's done, the results are either discarded (the default) or promoted into the persistent world.

This is the pattern that makes Scoped different: **everything is throwaway until explicitly kept.**

## Core Concepts

### Environment

An isolated workspace with its own object space, scope, and lifecycle.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `name` | Human-readable label |
| `owner_id` | The principal who created it |
| `template_id` | Blueprint it was created from (nullable for ad-hoc) |
| `scope_id` | Auto-created isolation scope for this environment |
| `state` | Current lifecycle state |
| `ephemeral` | 1 = throwaway (default), 0 = persistent |

### Lifecycle

```
spawning ──→ active ──→ completed ──→ discarded (default)
                │              │
                │              └──→ promoted (results kept)
                │
                └──→ suspended ──→ active (resumed)
```

- **spawning**: Being set up. Scope created, template applied, initial objects projected in.
- **active**: Work is happening. Objects are being created, modified, shared within the environment.
- **suspended**: Paused. All state preserved, but no new work allowed. Can be resumed.
- **completed**: Work is done. Owner decides: discard or promote.
- **discarded**: The happy path. Environment and its contents are archived. Cheap, instant, traced.
- **promoted**: Specific results are projected into persistent scopes. The environment itself may be discarded after promotion.

### Environment Isolation

Each environment gets its own auto-created scope. This means:
- Objects created in the environment are visible only to environment members
- External objects can be projected *into* the environment (read-only copies)
- Objects created inside cannot escape unless explicitly promoted
- The environment scope follows all the rules of Layer 4 (Tenancy)

### Environment Template

A reusable blueprint for spinning up environments.

| Field | Purpose |
|-------|---------|
| `name` | Template name |
| `config_json` | What the environment starts with: initial objects, member roles, rules, integrations |

Templates make it cheap to spin up standardized workspaces. "Create a code review environment" could mean: set up a scope, add the reviewer and author, project the PR objects in, apply code-review rules.

### Environment Snapshot

A freezable checkpoint of the entire environment state.

| Field | Purpose |
|-------|---------|
| `environment_id` | Which environment |
| `snapshot_data` | Full serialized state — all objects, versions, memberships, rules |
| `checksum` | Integrity verification |

Snapshots let you save a point-in-time copy of a throwaway environment before discarding it. They're also the foundation for environment-level rollback.

**Important:** Snapshots never include secret values (Layer 11). Secret refs are captured, but the actual encrypted values are resolved fresh on restore.

### Environment Objects

Every object within an environment is tracked:

| Field | Purpose |
|-------|---------|
| `environment_id` | Which environment |
| `object_id` | Which object |
| `origin` | "created" (born here) or "projected" (from outside) |

This distinction matters for discarding vs. promoting. Objects with origin "created" are candidates for promotion. Objects with origin "projected" were references from outside — they stay where they came from.

## How It Connects

### To Layer 3 (Objects)
Objects created in an environment are scoped objects with the environment owner as the owner. They follow all versioning and isolation rules.

### To Layer 4 (Tenancy)
Each environment has an auto-created scope. Environment members are scope members. Promotion is the act of projecting objects from the environment scope into a persistent scope.

### To Layer 5 (Rules)
Rules can be bound to environments. "Within this environment, all members have write access" is a rule binding. Template-defined rules are applied at spawn time.

### To Layer 6 (Audit)
Every environment action (spawn, suspend, complete, discard, promote, snapshot) is traced.

### To Layer 7 (Temporal)
Snapshots are temporal checkpoints. Discarding an environment is a soft operation — the audit trail retains the full history. Environment state can be reconstructed from the audit trail.

### To Layer 9 (Flow)
Promotion is the bridge between environments and flow. When an object is promoted, it enters the flow system — projected into a scope, optionally placed at a stage in a pipeline.

### To Layer 11 (Secrets)
Environments can have secret refs for credentials needed during the work. These refs are scoped to the environment — when the environment is discarded, the refs are revoked.

## Files

```
scoped/environments/
    __init__.py
    models.py          # Environment, EnvironmentTemplate, EnvironmentSnapshot
    lifecycle.py       # spawn, suspend, resume, complete, discard, promote
    container.py       # Isolation container — what the environment can see/touch
    snapshot.py        # Capture and restore full environment state
```

## Schema

```sql
CREATE TABLE environments (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    owner_id        TEXT NOT NULL REFERENCES principals(id),
    template_id     TEXT REFERENCES environment_templates(id),
    scope_id        TEXT REFERENCES scopes(id),
    state           TEXT NOT NULL DEFAULT 'spawning',
    ephemeral       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    completed_at    TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE environment_templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    owner_id        TEXT NOT NULL REFERENCES principals(id),
    config_json     TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE environment_snapshots (
    id              TEXT PRIMARY KEY,
    environment_id  TEXT NOT NULL REFERENCES environments(id),
    name            TEXT NOT NULL DEFAULT '',
    snapshot_data   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    checksum        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE environment_objects (
    id              TEXT PRIMARY KEY,
    environment_id  TEXT NOT NULL REFERENCES environments(id),
    object_id       TEXT NOT NULL REFERENCES scoped_objects(id),
    origin          TEXT NOT NULL DEFAULT 'created',
    added_at        TEXT NOT NULL,
    UNIQUE(environment_id, object_id)
);
```

## Invariants

1. Every environment gets its own isolation scope — no sharing by default.
2. Environments are ephemeral by default. Keeping results requires explicit promotion.
3. Discarding is the happy path — it's cheap, instant, and traced.
4. Environment snapshots never contain plaintext secret values.
5. Objects don't escape environments unless explicitly promoted.
