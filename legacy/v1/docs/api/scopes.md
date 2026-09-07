---
title: "Scopes API"
description: "API reference for ScopesNamespace -- scope lifecycle, membership, projection, hierarchy, and access-level management."
category: "API Reference"
---

# Scopes API

The `ScopesNamespace` manages scopes -- named containers that control visibility and
access to objects. Scopes support hierarchical nesting, role-based membership, and
projection of objects into shared spaces.

Access the namespace through the client:

```python
from scoped.client import ScopedClient

client = ScopedClient(database_url="sqlite:///app.db")
scopes = client.scopes
```

---

## CRUD Methods

### create

```python
scopes.create(
    name: str,
    description: str | None = None,
    parent_scope_id: str | None = None,
    visibility: str = "private",
    metadata: dict[str, Any] | None = None,
) -> Scope
```

Creates a new scope.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | *required* | Unique human-readable name for the scope. Must be 1-128 characters. |
| `description` | `str \| None` | `None` | Optional long-form description. |
| `parent_scope_id` | `str \| None` | `None` | ID of a parent scope for hierarchical nesting. Visibility rules are inherited from the parent unless explicitly overridden. |
| `visibility` | `str` | `"private"` | One of `"private"`, `"internal"`, `"public"`. Private scopes are visible only to members; internal scopes are visible to all authenticated principals; public scopes are visible to everyone. |
| `metadata` | `dict[str, Any] \| None` | `None` | Arbitrary metadata attached to the scope. |

#### Returns

A `Scope` model instance.

#### Raises

| Exception | Condition |
|---|---|
| `DuplicateScopeError` | A scope with the given name already exists. |
| `ScopeNotFoundError` | The specified `parent_scope_id` does not exist. |
| `ValidationError` | `name` is empty, exceeds 128 characters, or contains invalid characters. |

#### Example

```python
eng = client.scopes.create(
    name="engineering",
    description="All engineering assets",
    visibility="internal",
)

backend = client.scopes.create(
    name="engineering/backend",
    description="Backend team scope",
    parent_scope_id=eng.id,
)
print(backend.parent_scope_id)  # eng.id
```

---

### get

```python
scopes.get(scope_id: str) -> Scope
```

Retrieves a scope by ID.

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `scope_id` | `str` | The unique scope identifier. |

#### Returns

A `Scope` model instance.

#### Raises

| Exception | Condition |
|---|---|
| `ScopeNotFoundError` | No scope exists with the given ID. |

---

### rename

```python
scopes.rename(scope_id: str, new_name: str) -> Scope
```

Changes the name of an existing scope. The new name must be unique.

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `scope_id` | `str` | The scope to rename. |
| `new_name` | `str` | The new name (1-128 characters). |

#### Returns

The updated `Scope` model instance.

#### Raises

| Exception | Condition |
|---|---|
| `ScopeNotFoundError` | The scope does not exist. |
| `DuplicateScopeError` | The new name is already taken. |
| `ScopeLifecycleError` | The scope is frozen or archived. |

---

### update

```python
scopes.update(
    scope_id: str,
    description: str | None = None,
    visibility: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Scope
```

Updates mutable fields on a scope. Metadata is merged (same semantics as
`principals.update`).

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `scope_id` | `str` | *required* | The scope to update. |
| `description` | `str \| None` | `None` | New description. `None` leaves it unchanged. |
| `visibility` | `str \| None` | `None` | New visibility level. `None` leaves it unchanged. |
| `metadata` | `dict[str, Any] \| None` | `None` | Metadata fields to merge. |

#### Returns

The updated `Scope` model instance.

#### Raises

| Exception | Condition |
|---|---|
| `ScopeNotFoundError` | The scope does not exist. |
| `ScopeLifecycleError` | The scope is frozen or archived. |

---

### list

```python
scopes.list(
    parent_scope_id: str | None = None,
    visibility: str | None = None,
    order_by: str = "created_at",
    limit: int = 100,
    offset: int = 0,
) -> list[Scope]
```

Lists scopes with optional filtering and pagination.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `parent_scope_id` | `str \| None` | `None` | Filter to children of this scope. |
| `visibility` | `str \| None` | `None` | Filter by visibility level. |
| `order_by` | `str` | `"created_at"` | Sort order. See [order_by values](#order_by-values). |
| `limit` | `int` | `100` | Maximum results. |
| `offset` | `int` | `0` | Results to skip. |

#### Returns

A list of `Scope` instances.

---

### count

```python
scopes.count(
    parent_scope_id: str | None = None,
    visibility: str | None = None,
) -> int
```

Returns the total number of scopes matching the given filters. Useful for pagination
UIs without fetching full records.

#### Parameters

Same filter parameters as `list` (excluding `order_by`, `limit`, `offset`).

#### Returns

An integer count.

---

## Membership Methods

### add_member

```python
scopes.add_member(
    scope_id: str,
    principal_id: str,
    role: str = "viewer",
    access_level: str | None = None,
) -> ScopeMembership
```

Adds a principal to a scope with a given role.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `scope_id` | `str` | *required* | The scope to add the member to. |
| `principal_id` | `str` | *required* | The principal to add. |
| `role` | `str` | `"viewer"` | One of `"viewer"`, `"editor"`, `"admin"`, `"owner"`. See [Roles](#roles). |
| `access_level` | `str \| None` | `None` | Explicit access level override. When `None`, the access level is inferred from the role. See [Access Levels](#access-levels). |

#### Returns

A `ScopeMembership` model instance.

#### Raises

| Exception | Condition |
|---|---|
| `ScopeNotFoundError` | The scope does not exist. |
| `PrincipalNotFoundError` | The principal does not exist. |
| `DuplicateMembershipError` | The principal is already a member of this scope. |
| `ScopeLifecycleError` | The scope is archived. |

#### Example

```python
eng = client.scopes.create(name="engineering")
alice = client.principals.create(display_name="Alice", kind="user")

membership = client.scopes.add_member(eng.id, alice.id, role="editor")
print(membership.role)          # "editor"
print(membership.access_level)  # "write"
```

---

### add_members

```python
scopes.add_members(
    scope_id: str,
    members: list[dict[str, str]],
) -> list[ScopeMembership]
```

Adds multiple members to a scope in a single atomic operation.

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `scope_id` | `str` | The scope to add members to. |
| `members` | `list[dict[str, str]]` | Each dict must have a `"principal_id"` key and may include `"role"` (defaults to `"viewer"`) and `"access_level"`. |

#### Returns

A list of `ScopeMembership` instances.

#### Example

```python
memberships = client.scopes.add_members(
    eng.id,
    members=[
        {"principal_id": alice.id, "role": "admin"},
        {"principal_id": bob.id, "role": "editor"},
        {"principal_id": ci_bot.id, "role": "viewer"},
    ],
)
print(len(memberships))  # 3
```

---

### remove_member

```python
scopes.remove_member(scope_id: str, principal_id: str) -> None
```

Removes a principal from a scope. Projected objects owned by the principal are
**not** automatically unprojected.

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `scope_id` | `str` | The scope to remove the member from. |
| `principal_id` | `str` | The principal to remove. |

#### Raises

| Exception | Condition |
|---|---|
| `MembershipNotFoundError` | The principal is not a member of this scope. |

---

### members

```python
scopes.members(
    scope_id: str,
    role: str | None = None,
) -> list[ScopeMembership]
```

Lists members of a scope with an optional role filter.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `scope_id` | `str` | *required* | The scope to query. |
| `role` | `str \| None` | `None` | Filter by role. `None` returns all members. |

#### Returns

A list of `ScopeMembership` instances.

#### Example

```python
admins = client.scopes.members(eng.id, role="admin")
all_members = client.scopes.members(eng.id)
print(f"{len(admins)} admin(s) out of {len(all_members)} total")
```

---

## Projection Methods

### project

```python
scopes.project(
    scope_id: str,
    object_id: str,
    access_level: str = "read",
) -> ScopeProjection
```

Projects an object into a scope, making it visible to all scope members at the
specified access level.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `scope_id` | `str` | *required* | The scope to project into. |
| `object_id` | `str` | *required* | The object to project. |
| `access_level` | `str` | `"read"` | The access level granted to scope members: `"read"`, `"write"`, or `"admin"`. |

#### Returns

A `ScopeProjection` model instance.

#### Raises

| Exception | Condition |
|---|---|
| `ScopeNotFoundError` | The scope does not exist. |
| `ObjectNotFoundError` | The object does not exist. |
| `DuplicateProjectionError` | The object is already projected into this scope. |

#### Example

```python
with client.as_principal(alice):
    doc, _ = client.objects.create(object_type="doc", data={"title": "Shared"})

    projection = client.scopes.project(eng.id, doc.id, access_level="write")
    print(projection.access_level)  # "write"
```

---

### unproject

```python
scopes.unproject(scope_id: str, object_id: str) -> None
```

Removes an object's projection from a scope. Scope members will no longer see the
object through this scope.

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `scope_id` | `str` | The scope to remove the projection from. |
| `object_id` | `str` | The object to unproject. |

#### Raises

| Exception | Condition |
|---|---|
| `ProjectionNotFoundError` | The object is not projected into this scope. |

---

### projections

```python
scopes.projections(scope_id: str) -> list[ScopeProjection]
```

Lists all objects projected into a scope.

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `scope_id` | `str` | The scope to query. |

#### Returns

A list of `ScopeProjection` instances.

---

## Lifecycle Methods

### freeze

```python
scopes.freeze(scope_id: str) -> Scope
```

Freezes a scope. Frozen scopes are read-only: no new members can be added, no
objects can be projected, and the scope cannot be renamed or updated. Existing
members retain read access.

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `scope_id` | `str` | The scope to freeze. |

#### Returns

The updated `Scope` with `lifecycle` set to `"frozen"`.

#### Raises

| Exception | Condition |
|---|---|
| `ScopeNotFoundError` | The scope does not exist. |
| `ScopeLifecycleError` | The scope is already archived. |

---

### archive

```python
scopes.archive(scope_id: str) -> Scope
```

Archives a scope. Archived scopes are hidden from `list` results and cannot be
modified. Members and projections are retained for audit purposes.

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `scope_id` | `str` | The scope to archive. |

#### Returns

The updated `Scope` with `lifecycle` set to `"archived"`.

#### Raises

| Exception | Condition |
|---|---|
| `ScopeNotFoundError` | The scope does not exist. |

---

## Roles

Roles define the default capabilities of a scope member.

| Role | Default Access Level | Capabilities |
|---|---|---|
| `viewer` | `read` | Read projected objects. |
| `editor` | `write` | Read and write projected objects. |
| `admin` | `admin` | Read, write, and manage scope membership and projections. |
| `owner` | `admin` | All admin capabilities plus scope lifecycle control (freeze, archive, delete). |

## Access Levels

Access levels control what operations a principal can perform on projected objects.

| Access Level | Permitted Operations |
|---|---|
| `read` | `get`, `list`, `versions` |
| `write` | All `read` operations plus `create`, `update`, `delete` |
| `admin` | All `write` operations plus `project`, `unproject`, `add_member`, `remove_member` |

---

## order_by Values

| Value | Description |
|---|---|
| `"created_at"` | Ascending by creation timestamp (oldest first). |
| `"-created_at"` | Descending by creation timestamp (newest first). |
| `"name"` | Ascending alphabetical by scope name. |
| `"-name"` | Descending alphabetical by scope name. |

---

## Scope Hierarchy

Scopes support hierarchical nesting via the `parent_scope_id` field. Child scopes
inherit visibility from their parent unless explicitly overridden. Membership does
**not** cascade -- being a member of a parent scope does not automatically grant
access to child scopes.

```python
org = client.scopes.create(name="acme-corp", visibility="internal")
eng = client.scopes.create(name="engineering", parent_scope_id=org.id)
backend = client.scopes.create(name="backend", parent_scope_id=eng.id)

# List direct children
children = client.scopes.list(parent_scope_id=org.id)
print([s.name for s in children])  # ["engineering"]

nested = client.scopes.list(parent_scope_id=eng.id)
print([s.name for s in nested])    # ["backend"]
```

---

## Models

### Scope

```python
@dataclass(frozen=True)
class Scope:
    id: str
    name: str
    description: str | None
    parent_scope_id: str | None
    visibility: str
    lifecycle: str
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]
```

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique scope identifier (UUID v4). |
| `name` | `str` | Human-readable unique name. |
| `description` | `str \| None` | Long-form description. |
| `parent_scope_id` | `str \| None` | Parent scope ID, or `None` for root scopes. |
| `visibility` | `str` | `"private"`, `"internal"`, or `"public"`. |
| `lifecycle` | `str` | `"active"`, `"frozen"`, or `"archived"`. |
| `created_at` | `datetime` | UTC timestamp of creation. |
| `updated_at` | `datetime` | UTC timestamp of last modification. |
| `metadata` | `dict[str, Any]` | Arbitrary metadata. |

### ScopeMembership

```python
@dataclass(frozen=True)
class ScopeMembership:
    id: str
    scope_id: str
    principal_id: str
    role: str
    access_level: str
    created_at: datetime
```

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique membership identifier. |
| `scope_id` | `str` | The scope this membership belongs to. |
| `principal_id` | `str` | The member principal's ID. |
| `role` | `str` | `"viewer"`, `"editor"`, `"admin"`, or `"owner"`. |
| `access_level` | `str` | `"read"`, `"write"`, or `"admin"`. |
| `created_at` | `datetime` | UTC timestamp of membership creation. |

### ScopeProjection

```python
@dataclass(frozen=True)
class ScopeProjection:
    id: str
    scope_id: str
    object_id: str
    access_level: str
    created_at: datetime
    created_by: str
```

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique projection identifier. |
| `scope_id` | `str` | The scope the object is projected into. |
| `object_id` | `str` | The projected object's ID. |
| `access_level` | `str` | `"read"`, `"write"`, or `"admin"`. |
| `created_at` | `datetime` | UTC timestamp of projection creation. |
| `created_by` | `str` | Principal ID that created the projection. |

---

## Complete Example

```python
from scoped.client import ScopedClient

with ScopedClient(database_url="sqlite:///app.db") as client:
    # Create principals
    alice = client.principals.create(display_name="Alice", kind="user")
    bob = client.principals.create(display_name="Bob", kind="user")

    with client.as_principal(alice):
        # Create scope hierarchy
        eng = client.scopes.create(name="engineering", visibility="internal")
        backend = client.scopes.create(
            name="backend", parent_scope_id=eng.id
        )

        # Membership
        client.scopes.add_member(backend.id, alice.id, role="owner")
        client.scopes.add_member(backend.id, bob.id, role="editor")

        # Create and project an object
        doc, _ = client.objects.create(
            object_type="spec", data={"title": "API Design"}
        )
        client.scopes.project(backend.id, doc.id, access_level="write")

    # Bob can now see the object through the scope
    with client.as_principal(bob):
        visible = client.objects.list(object_type="spec")
        print(len(visible))  # 1

    # Scope management
    with client.as_principal(alice):
        members = client.scopes.members(backend.id)
        print(f"Members: {len(members)}")  # 2

        projections = client.scopes.projections(backend.id)
        print(f"Projected objects: {len(projections)}")  # 1

        # Lifecycle
        client.scopes.freeze(backend.id)
        frozen = client.scopes.get(backend.id)
        print(frozen.lifecycle)  # "frozen"
```
