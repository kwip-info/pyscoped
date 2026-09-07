# Layer 4: Scoping & Tenancy

## Purpose

Tenancy is the answer to: **how do I share something?**

Every object starts isolated (Layer 3). To share it, the owner must create a **scope** — a named isolation boundary that defines who can see what. Scopes are the sharing primitive. They are explicit, owned, audited, and revocable.

There is no "make this public" toggle. There is no implicit group access. There is only: create a scope, add members, project objects in.

## Core Concepts

### Scope

A scope is a sharing container.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `name` | Human-readable label |
| `owner_id` | The principal who created this scope — they control it |
| `parent_scope_id` | For nesting (nullable = top-level) |
| `lifecycle` | ACTIVE, FROZEN, ARCHIVED |

Scopes can nest. A team scope can be a child of an org scope. Nesting is how hierarchical multi-tenancy works — but the hierarchy shape is application-defined (via principal relationships in Layer 2), not framework-prescribed.

### ScopeMembership

Who is in a scope and what role they have.

| Field | Purpose |
|-------|---------|
| `scope_id` | Which scope |
| `principal_id` | Which principal |
| `role` | viewer, editor, admin, owner |
| `expires_at` | Optional expiry — time-bombed access |
| `lifecycle` | ACTIVE = access granted, ARCHIVED = revoked |

**Expiration enforcement:** `expires_at` is checked in all 6 visibility query sites: `is_member()`, `can_see()`, `scope_member_ids()`, `get_memberships()`, `get_principal_scopes()`, `_projected_object_ids()`. Expired memberships are lazily archived on access (updated to ARCHIVED when encountered). Use `ScopeMembership.is_expired` property to check programmatically.

Roles are advisory — the rule engine (Layer 5) determines actual permissions. But roles provide the default semantics: viewers can read, editors can write, admins can manage membership, owners can do everything including dissolve the scope.

### ScopeProjection

The explicit act of making an object visible in a scope.

| Field | Purpose |
|-------|---------|
| `scope_id` | Which scope to project into |
| `object_id` | Which object to project |
| `projected_by` | Must be the object's owner |
| `access_level` | read, write, admin |

Projection is the bridge between isolation (Layer 3) and sharing (this layer). Only the object's owner can project it — this is the "I'm choosing to share this" action.

### Visibility Resolution

"What can principal X see?" is resolved by walking:
1. Objects owned by X (always visible)
2. Scopes X is a member of → objects projected into those scopes
3. Parent scopes (if scope nesting is used) → inherited projections
4. Rule engine evaluation (Layer 5) for DENY overrides

## How It Connects

### To Layer 2 (Identity)
Scope ownership and membership are principal-based. Principal relationships can inform scope hierarchies — e.g., a rule might say "if principal A is `member_of` principal B, and B is the scope owner, A inherits viewer access."

### To Layer 3 (Objects)
Scopes are how objects become visible beyond their owner. Without projections, objects are isolated. With projections, they're visible within the scope's boundary.

### To Layer 5 (Rules)
Rules modify what scopes allow. A rule can restrict a scope to read-only, prevent sharing outside an org boundary, or grant automatic membership based on principal relationships. The scope provides the structure; rules provide the policy.

### To Layer 6 (Audit)
Scope creation, membership changes, and projections are all traced. "Who shared what with whom, and when?" is always answerable.

### To Layer 8 (Environments)
Every environment gets its own auto-created scope. Objects created in the environment are projected into this scope. When the environment is discarded, its scope is archived. When objects are promoted out, they're projected into the target scope.

### To Layer 9 (Flow)
Flow channels connect scopes. A promotion moves an object from an environment's scope into a persistent scope. Stage transitions can require scope-level approvals.

### To Layer 13 (Connector)
Connectors create a shared "connector scope" visible to both organizations. Objects projected into this scope are visible across the bridge.

## Extensions

This layer has been extended with:

- **[A5: Configuration Hierarchy](../extensions/A5-config.md)** — Per-scope key-value settings that inherit down the scope hierarchy. Child scopes inherit parent settings unless explicitly overridden.

## Files

```
scoped/tenancy/
    __init__.py
    models.py        # Scope, ScopeMembership, ScopeHierarchy, ScopeProjection
    engine.py        # Resolve "what can principal X see?"
    lifecycle.py     # Create, freeze, archive, dissolve scopes
    projection.py    # Project objects into scopes
    config.py        # [A5] ScopedSetting, ConfigStore
```

## Schema

```sql
CREATE TABLE scopes (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    owner_id        TEXT NOT NULL REFERENCES principals(id),
    parent_scope_id TEXT REFERENCES scopes(id),
    registry_entry_id TEXT,
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE scope_memberships (
    id              TEXT PRIMARY KEY,
    scope_id        TEXT NOT NULL REFERENCES scopes(id),
    principal_id    TEXT NOT NULL REFERENCES principals(id),
    role            TEXT NOT NULL DEFAULT 'viewer',
    granted_at      TEXT NOT NULL,
    granted_by      TEXT NOT NULL,
    expires_at      TEXT,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    UNIQUE(scope_id, principal_id, role)
);

CREATE TABLE scope_projections (
    id              TEXT PRIMARY KEY,
    scope_id        TEXT NOT NULL REFERENCES scopes(id),
    object_id       TEXT NOT NULL REFERENCES scoped_objects(id),
    projected_at    TEXT NOT NULL,
    projected_by    TEXT NOT NULL,
    access_level    TEXT NOT NULL DEFAULT 'read',
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    UNIQUE(scope_id, object_id)
);
```

## Invariants

1. Sharing requires explicit scope creation and projection. No implicit access.
2. Only object owners can project objects into scopes.
3. Scope membership revocation is immediate (same transaction).
4. Frozen scopes cannot be modified — members and projections are locked.
5. Scope dissolution archives all memberships and projections (traced).
