# Layer 2: Identity & Principals

## Purpose

Identity answers one question: **who is acting?**

Every operation in Scoped requires a principal — the entity performing the action. There are no anonymous operations. The identity layer provides the machinery for establishing, tracking, and resolving principals, but it does **not** define what principals look like. That's the application's job.

The framework provides: context management, relationship resolution, principal interfaces.
The application provides: concrete types (User, Bot, Team, ServiceAccount, Organization — whatever fits its domain).

## Core Concepts

### Principal

A `Principal` is any registered entity that can act. It is defined by:

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `kind` | Application-defined type string ("user", "team", "bot", "org", etc.) |
| `display_name` | Human-readable label |
| `registry_entry_id` | Link to the registry — principals are registered constructs |
| `lifecycle` | ACTIVE, SUSPENDED, ARCHIVED |
| `metadata` | Arbitrary key-value data |

The framework doesn't know what a "user" or a "team" means. It knows that principals exist, that they have kinds, and that they can be related to each other.

### PrincipalRelationship

Relationships between principals are generic edges in a graph:

| Field | Purpose |
|-------|---------|
| `parent_id` | The parent principal |
| `child_id` | The child principal |
| `relationship` | Application-defined type: "member_of", "owns", "administers", etc. |

This creates a directed graph. A "user" might be `member_of` a "team", which is `member_of` an "org". The framework walks this graph to resolve hierarchy, but the shape of the hierarchy is entirely application-defined.

### ScopedContext

The `ScopedContext` is the thread-local (contextvars-based) answer to "who is acting right now?" Every framework operation reads the context to determine the acting principal.

```python
with ScopedContext(principal=current_user):
    # Everything inside here knows who is acting
    obj = create_object(...)   # traced to current_user
    share(obj, scope)          # authorized against current_user's permissions
```

If there is no `ScopedContext` when an operation is attempted, the framework raises `NoContextError`. This is a hard failure — the compliance engine enforces it at runtime.

## How It Connects

### To Layer 1 (Registry)
Principals are registered constructs. Each principal has a `registry_entry_id`. Principal kinds ("user", "team") are registered entries of kind `PRINCIPAL`. The registry is how the framework knows what principal types exist.

### To Layer 3 (Objects)
Every object has an `owner_id` — a principal. Object isolation is enforced based on the acting principal's identity and relationships. When you create an object, the acting principal from `ScopedContext` becomes the owner.

### To Layer 4 (Tenancy)
Scope membership is between principals and scopes. Scope ownership is a principal. The tenancy engine resolves "what can this principal see?" by walking scope memberships against the principal's identity and relationships.

### To Layer 5 (Rules)
Rules can be bound to specific principals or principal kinds. Rule evaluation takes the acting principal as input. Principal relationships affect rule inheritance — a rule bound to an "org" principal may cascade to all "user" principals that are `member_of` that org.

### To Layer 6 (Audit)
Every trace entry records `actor_id` — the principal who performed the action. The audit layer is a consumer of identity context.

### To Layer 8 (Environments)
Environments have owners (principals) and members (principals). The `ScopedContext` determines who can spawn, modify, or access an environment.

### To Layer 11 (Secrets)
Secret ownership and access control are principal-based. Secret refs are granted to specific principals. The vault checks the acting principal's identity before decrypting.

### To Layer 13 (Connector)
Connectors reference `local_org_id` — a principal representing the organization. Cross-org identity mapping happens at the connector boundary.

## Files

```
scoped/identity/
    __init__.py
    principal.py     # Principal interface, PrincipalKind, PrincipalRelationship
    context.py       # ScopedContext — contextvars "who is acting right now"
    resolver.py      # Walk principal relationship graph
```

## Schema

```sql
CREATE TABLE principals (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    display_name    TEXT NOT NULL DEFAULT '',
    registry_entry_id TEXT NOT NULL REFERENCES registry_entries(id),
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL DEFAULT 'system',
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE principal_relationships (
    id              TEXT PRIMARY KEY,
    parent_id       TEXT NOT NULL REFERENCES principals(id),
    child_id        TEXT NOT NULL REFERENCES principals(id),
    relationship    TEXT NOT NULL DEFAULT 'member_of',
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    UNIQUE(parent_id, child_id, relationship)
);
```

## Invariants

1. Every operation MUST have a `ScopedContext` with a valid principal.
2. Principal kinds are application-defined, not framework-prescribed.
3. Principal relationships form a directed graph — the framework walks it but doesn't prescribe its shape.
4. Principal creation and relationship changes are traced actions.
5. Archived principals cannot perform actions but their history is retained.
