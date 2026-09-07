---
title: "Principals API"
description: "API reference for PrincipalsNamespace -- creating, retrieving, updating, and listing principals in pyscoped."
category: "API Reference"
---

# Principals API

The `PrincipalsNamespace` manages identity records -- users, services, groups, and
other actor types. Every mutating operation in pyscoped is attributed to a principal,
making this namespace foundational to the permission and audit model.

Access the namespace through the client:

```python
from scoped.client import ScopedClient

client = ScopedClient(database_url="sqlite:///app.db")
principals = client.principals
```

Or through the module-level shortcut after calling `scoped.init()`:

```python
import scoped

scoped.init(database_url="sqlite:///app.db")
principals = scoped.principals
```

---

## Methods

### create

```python
principals.create(
    display_name: str,
    kind: str = "user",
    metadata: dict[str, Any] | None = None,
    principal_id: str | None = None,
) -> Principal
```

Creates a new principal and records the operation in the audit trail.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `display_name` | `str` | *required* | Human-readable name for the principal. |
| `kind` | `str` | `"user"` | Classification of the principal. Common values: `"user"`, `"service"`, `"group"`, `"bot"`. |
| `metadata` | `dict[str, Any] \| None` | `None` | Arbitrary JSON-serializable metadata attached to the principal. |
| `principal_id` | `str \| None` | `None` | Explicit ID to assign. When `None`, a UUID v4 is generated automatically. Useful for mapping to external identity providers. |

#### Returns

A `Principal` model instance representing the newly created record.

#### Raises

| Exception | Condition |
|---|---|
| `DuplicatePrincipalError` | A principal with the given `principal_id` already exists. |
| `ValidationError` | `display_name` is empty or exceeds 255 characters. |

#### Example

```python
alice = client.principals.create(
    display_name="Alice Chen",
    kind="user",
    metadata={"department": "engineering", "email": "alice@example.com"},
)
print(alice.id)            # "b3f1a2c4-..."
print(alice.kind)          # "user"
print(alice.display_name)  # "Alice Chen"

# Explicit ID for external identity mapping
external = client.principals.create(
    display_name="GitHub Actions",
    kind="service",
    principal_id="github_actions_main",
)
assert external.id == "github_actions_main"
```

---

### get

```python
principals.get(principal_id: str) -> Principal
```

Retrieves a principal by ID. Raises an exception if the principal does not exist.

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `principal_id` | `str` | The unique identifier of the principal. |

#### Returns

A `Principal` model instance.

#### Raises

| Exception | Condition |
|---|---|
| `PrincipalNotFoundError` | No principal exists with the given ID. |

#### Example

```python
alice = client.principals.get("b3f1a2c4-...")
print(alice.display_name)  # "Alice Chen"

# Raises PrincipalNotFoundError
try:
    client.principals.get("nonexistent-id")
except PrincipalNotFoundError as exc:
    print(exc)  # "Principal 'nonexistent-id' not found"
```

---

### find

```python
principals.find(principal_id: str) -> Principal | None
```

Retrieves a principal by ID, returning `None` instead of raising when the principal
does not exist. Preferred over `get` when absence is an expected case.

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `principal_id` | `str` | The unique identifier of the principal. |

#### Returns

A `Principal` model instance, or `None` if no matching principal is found.

#### Example

```python
maybe_alice = client.principals.find("b3f1a2c4-...")
if maybe_alice is not None:
    print(maybe_alice.display_name)

missing = client.principals.find("does-not-exist")
assert missing is None
```

---

### update

```python
principals.update(
    principal: str | Principal,
    display_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Principal
```

Updates a principal's mutable fields. Metadata is **merged** with the existing
metadata dict (top-level keys are overwritten; keys not present in the update are
retained). Set a metadata key to `None` to delete it.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `principal` | `str \| Principal` | *required* | The principal ID or model instance to update. |
| `display_name` | `str \| None` | `None` | New display name. `None` leaves the current value unchanged. |
| `metadata` | `dict[str, Any] \| None` | `None` | Metadata fields to merge. `None` leaves existing metadata unchanged. |

#### Returns

The updated `Principal` model instance with the new field values.

#### Raises

| Exception | Condition |
|---|---|
| `PrincipalNotFoundError` | The specified principal does not exist. |
| `ValidationError` | `display_name` is empty or exceeds 255 characters. |

#### Example

```python
alice = client.principals.create(
    display_name="Alice",
    metadata={"department": "engineering", "level": "senior"},
)

# Merge metadata -- "department" is overwritten, "level" is retained
updated = client.principals.update(
    alice,
    display_name="Alice Chen",
    metadata={"department": "platform", "title": "Staff Engineer"},
)

print(updated.display_name)              # "Alice Chen"
print(updated.metadata["department"])    # "platform"
print(updated.metadata["level"])         # "senior"   (retained)
print(updated.metadata["title"])         # "Staff Engineer" (added)

# Delete a metadata key by setting it to None
client.principals.update(alice, metadata={"level": None})
```

---

### list

```python
principals.list(kind: str | None = None) -> list[Principal]
```

Returns all principals, optionally filtered by kind.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `kind` | `str \| None` | `None` | Filter results to principals of this kind. `None` returns all kinds. |

#### Returns

A list of `Principal` model instances. Returns an empty list if no principals match.

#### Example

```python
client.principals.create(display_name="Alice", kind="user")
client.principals.create(display_name="CI Bot", kind="service")
client.principals.create(display_name="Deploy Agent", kind="service")

all_principals = client.principals.list()
print(len(all_principals))  # 3

services = client.principals.list(kind="service")
print(len(services))  # 2
for svc in services:
    print(svc.display_name, svc.kind)
# "CI Bot service"
# "Deploy Agent service"
```

---

## Models

### Principal

The core identity model.

```python
@dataclass(frozen=True)
class Principal:
    id: str
    kind: str
    display_name: str
    created_at: datetime
    created_by: str | None
    lifecycle: str
    metadata: dict[str, Any]
```

#### Fields

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique identifier (UUID v4 or caller-supplied). |
| `kind` | `str` | Classification: `"user"`, `"service"`, `"group"`, `"bot"`, or custom. |
| `display_name` | `str` | Human-readable label. |
| `created_at` | `datetime` | UTC timestamp of creation. |
| `created_by` | `str \| None` | ID of the principal that created this record, or `None` if self-created or system-created. |
| `lifecycle` | `str` | Lifecycle state: `"active"`, `"suspended"`, `"archived"`. |
| `metadata` | `dict[str, Any]` | Arbitrary JSON-serializable key-value data. |

---

### PrincipalRelationship

Represents a hierarchical or associative link between two principals. This model is
not directly exposed through the `PrincipalsNamespace` API; use the
[services escape hatch](client.md#services-escape-hatch) to create and query
relationships.

```python
@dataclass(frozen=True)
class PrincipalRelationship:
    id: str
    parent_id: str
    child_id: str
    relationship_type: str
    created_at: datetime
    metadata: dict[str, Any]
```

#### Fields

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique identifier for the relationship. |
| `parent_id` | `str` | ID of the parent principal. |
| `child_id` | `str` | ID of the child principal. |
| `relationship_type` | `str` | Type of relationship: `"member_of"`, `"reports_to"`, `"owns"`, or custom. |
| `created_at` | `datetime` | UTC timestamp of creation. |
| `metadata` | `dict[str, Any]` | Arbitrary metadata on the relationship. |

#### Example (via services)

```python
org = client.principals.create(display_name="Acme Corp", kind="group")
alice = client.principals.create(display_name="Alice", kind="user")

rel = client.services.principal_service.create_relationship(
    parent_id=org.id,
    child_id=alice.id,
    relationship_type="member_of",
)
print(rel.relationship_type)  # "member_of"

members = client.services.principal_service.list_relationships(
    parent_id=org.id,
    relationship_type="member_of",
)
print(len(members))  # 1
```

---

## Context Inference for Actor

Many pyscoped operations require knowledge of the acting principal (the "actor"). The
library resolves the actor using the following precedence:

1. **Explicit parameter** -- methods that accept a `principal_id` or `owner_id`
   argument use that value directly.
2. **Context manager** -- if the call is inside a `client.as_principal(...)` block,
   the bound principal is used.
3. **System principal** -- if neither of the above is available, the operation is
   attributed to the built-in `SYSTEM` principal.

```python
admin = client.principals.create(display_name="Admin", kind="user")

# 1. Explicit -- owner_id is used as the actor
obj, v = client.objects.create(
    object_type="doc",
    data={"x": 1},
    owner_id=admin.id,
)

# 2. Context manager -- admin is the actor for all operations in the block
with client.as_principal(admin):
    obj2, v2 = client.objects.create(object_type="doc", data={"x": 2})

# 3. System -- no principal set, SYSTEM is the actor
obj3, v3 = client.objects.create(object_type="doc", data={"x": 3})
```

The actor is recorded in every audit trail entry as the `actor_id` field, ensuring
full attribution for compliance and debugging.

---

## Complete Example

```python
from scoped.client import ScopedClient

with ScopedClient(database_url="sqlite:///app.db") as client:
    # Create principals of different kinds
    alice = client.principals.create(
        display_name="Alice",
        kind="user",
        metadata={"team": "backend"},
    )
    ci = client.principals.create(
        display_name="CI Pipeline",
        kind="service",
    )

    # Retrieve and update
    fetched = client.principals.get(alice.id)
    assert fetched.display_name == "Alice"

    updated = client.principals.update(
        alice,
        display_name="Alice (Admin)",
        metadata={"role": "admin"},
    )
    assert updated.metadata["team"] == "backend"   # retained
    assert updated.metadata["role"] == "admin"      # merged

    # Safe lookup
    ghost = client.principals.find("no-such-id")
    assert ghost is None

    # List by kind
    users = client.principals.list(kind="user")
    services = client.principals.list(kind="service")
    print(f"{len(users)} user(s), {len(services)} service(s)")
    # "1 user(s), 1 service(s)"
```
