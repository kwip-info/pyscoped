---
title: "Objects API"
description: "API reference for ObjectsNamespace -- versioned, ownership-scoped object storage with soft-delete and rules enforcement."
category: "API Reference"
---

# Objects API

The `ObjectsNamespace` provides versioned, ownership-scoped object storage. Every
mutation produces an immutable `ObjectVersion`, enabling full history reconstruction.
Access is isolation-enforced: a principal can only see objects they own or that are
projected into a scope they belong to.

Access the namespace through the client:

```python
from scoped.client import ScopedClient

client = ScopedClient(database_url="sqlite:///app.db")
objects = client.objects
```

---

## Methods

### create

```python
objects.create(
    object_type: str,
    data: dict[str, Any],
    owner_id: str | None = None,
    change_reason: str | None = None,
) -> tuple[ScopedObject, ObjectVersion]
```

Creates a new object and its initial version. Before the write is committed, any
applicable DENY rules are evaluated; if a rule matches, `AccessDeniedError` is raised
and the operation is rolled back.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `object_type` | `str` | *required* | A string classifying the object (e.g. `"document"`, `"config"`, `"secret"`). Used for filtering and rule matching. |
| `data` | `dict[str, Any] \| Any` | *required* | JSON-serializable payload, or a registered typed instance (Pydantic model, dataclass, `ScopedSerializable`). Typed instances are auto-serialized. |
| `owner_id` | `str \| None` | `None` | Principal ID of the owner. Falls back to the context principal or `SYSTEM`. |
| `change_reason` | `str \| None` | `None` | Human-readable reason recorded in the audit trail and version metadata. |

#### Returns

A tuple of `(ScopedObject, ObjectVersion)` representing the newly created object and
its first version.

#### Raises

| Exception | Condition |
|---|---|
| `AccessDeniedError` | A DENY rule blocks this principal from creating objects of this type. |
| `ValidationError` | `object_type` is empty or `data` is not JSON-serializable. |

#### Example

```python
with client.as_principal(alice):
    doc, v1 = client.objects.create(
        object_type="document",
        data={"title": "RFC-42", "body": "..."},
        change_reason="Initial draft",
    )
    print(doc.id)             # UUID
    print(doc.object_type)    # "document"
    print(v1.version_number)  # 1
    print(v1.data["title"])   # "RFC-42"
```

---

### create_many

```python
objects.create_many(
    items: list[dict[str, Any]],
    owner_id: str | None = None,
) -> list[tuple[ScopedObject, ObjectVersion]]
```

Atomically creates multiple objects in a single transaction. If any item fails
validation or is blocked by a DENY rule, the entire batch is rolled back.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `items` | `list[dict[str, Any]]` | *required* | Each dict must contain `"object_type"` and `"data"` keys. Optional keys: `"change_reason"`. |
| `owner_id` | `str \| None` | `None` | Owner for all objects in the batch. |

#### Returns

A list of `(ScopedObject, ObjectVersion)` tuples in the same order as `items`.

#### Raises

| Exception | Condition |
|---|---|
| `AccessDeniedError` | A DENY rule blocks creation of any item in the batch. |
| `ValidationError` | Any item is missing required keys or contains non-serializable data. |
| `BatchError` | Wraps the underlying error with the index of the failing item. |

#### Example

```python
results = client.objects.create_many(
    items=[
        {"object_type": "config", "data": {"key": "timeout", "value": 30}},
        {"object_type": "config", "data": {"key": "retries", "value": 3}},
        {"object_type": "config", "data": {"key": "debug", "value": False}},
    ],
    owner_id=admin.id,
)
print(len(results))  # 3
for obj, ver in results:
    print(obj.object_type, ver.version_number)
```

---

### get

```python
objects.get(
    object_id: str,
    principal_id: str | None = None,
) -> ScopedObject | None
```

Retrieves an object by ID. Returns `None` if the object does not exist or if the
requesting principal does not have read access (isolation-enforced). Soft-deleted
objects also return `None`.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `object_id` | `str` | *required* | The unique identifier of the object. |
| `principal_id` | `str \| None` | `None` | The principal requesting access. Falls back to the context principal. |

#### Returns

A `ScopedObject` instance, or `None` if not found or not accessible.

#### Example

```python
with client.as_principal(alice):
    doc = client.objects.get(doc_id)
    if doc is not None:
        print(doc.data["title"])

# Explicit principal
doc = client.objects.get(doc_id, principal_id=alice.id)
```

---

### update

```python
objects.update(
    object_id: str,
    data: dict[str, Any],
    principal_id: str | None = None,
    change_reason: str | None = None,
) -> tuple[ScopedObject, ObjectVersion]
```

Creates a new immutable version of the object with the given data. The previous
version is preserved in the version history. DENY rules are checked before the
write is committed.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `object_id` | `str` | *required* | The object to update. |
| `data` | `dict[str, Any] \| Any` | *required* | Complete replacement data, or a registered typed instance. |
| `principal_id` | `str \| None` | `None` | The acting principal. Falls back to the context principal. |
| `change_reason` | `str \| None` | `None` | Human-readable reason for the change. |

#### Returns

A tuple of `(ScopedObject, ObjectVersion)` where the object reflects the latest
state and the version is the newly created record.

#### Raises

| Exception | Condition |
|---|---|
| `ObjectNotFoundError` | The object does not exist or is not accessible to the principal. |
| `AccessDeniedError` | A DENY rule blocks this principal from updating this object. |

#### Example

```python
with client.as_principal(alice):
    doc, v2 = client.objects.update(
        doc.id,
        data={"title": "RFC-42 (Revised)", "body": "...updated..."},
        change_reason="Addressed review comments",
    )
    print(v2.version_number)  # 2
    print(v2.data["title"])   # "RFC-42 (Revised)"
```

---

### delete

```python
objects.delete(
    object_id: str,
    principal_id: str | None = None,
    reason: str | None = None,
) -> Tombstone
```

Soft-deletes an object. The object and all its versions are retained in storage but
are no longer returned by `get` or `list`. A `Tombstone` marker is created. DENY
rules are checked before the operation.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `object_id` | `str` | *required* | The object to delete. |
| `principal_id` | `str \| None` | `None` | The acting principal. |
| `reason` | `str \| None` | `None` | Reason for deletion, recorded in the audit trail. |

#### Returns

A `Tombstone` model instance.

#### Raises

| Exception | Condition |
|---|---|
| `ObjectNotFoundError` | The object does not exist or is already deleted. |
| `AccessDeniedError` | A DENY rule blocks deletion. |

#### Example

```python
tombstone = client.objects.delete(
    doc.id,
    principal_id=admin.id,
    reason="Superseded by RFC-43",
)
print(tombstone.deleted_at)   # datetime
print(tombstone.deleted_by)   # admin.id
print(tombstone.reason)       # "Superseded by RFC-43"

# Object is no longer visible
assert client.objects.get(doc.id, principal_id=admin.id) is None
```

---

### list

```python
objects.list(
    principal_id: str | None = None,
    object_type: str | None = None,
    order_by: str = "created_at",
    limit: int = 100,
    offset: int = 0,
) -> list[ScopedObject]
```

Returns objects visible to the principal, with optional filtering and pagination.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `principal_id` | `str \| None` | `None` | Scopes results to objects this principal can see. |
| `object_type` | `str \| None` | `None` | Filter by object type. |
| `order_by` | `str` | `"created_at"` | Sort order. See [order_by values](#order_by-values). |
| `limit` | `int` | `100` | Maximum number of results. |
| `offset` | `int` | `0` | Number of results to skip. |

#### Returns

A list of `ScopedObject` instances.

#### Example

```python
with client.as_principal(alice):
    recent_docs = client.objects.list(
        object_type="document",
        order_by="-created_at",
        limit=10,
    )
    for doc in recent_docs:
        print(doc.id, doc.data.get("title"))
```

---

### versions

```python
objects.versions(
    object_id: str,
    principal_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ObjectVersion]
```

Returns the version history of an object, newest first.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `object_id` | `str` | *required* | The object whose versions to retrieve. |
| `principal_id` | `str \| None` | `None` | The requesting principal (for access control). |
| `limit` | `int` | `100` | Maximum number of versions to return. |
| `offset` | `int` | `0` | Number of versions to skip. |

#### Returns

A list of `ObjectVersion` instances, ordered by `version_number` descending.

#### Example

```python
history = client.objects.versions(doc.id, principal_id=alice.id)
for ver in history:
    print(f"v{ver.version_number}: {ver.change_reason} ({ver.created_at})")
```

---

## order_by Values

| Value | Description |
|---|---|
| `"created_at"` | Ascending by creation timestamp (oldest first). |
| `"-created_at"` | Descending by creation timestamp (newest first). |
| `"object_type"` | Ascending alphabetical by object type. |

---

## Rules Enforcement

Before every `create`, `update`, and `delete` operation, the objects namespace
evaluates all DENY rules that match the acting principal, object type, and scope.
If any rule matches, the operation is rejected with a `AccessDeniedError` and no data
is written.

```python
from scoped.exceptions import AccessDeniedError

try:
    client.objects.create(
        object_type="financial_record",
        data={"amount": 10000},
        owner_id=intern.id,
    )
except AccessDeniedError as exc:
    print(exc)
    # "DENY rule 'no-intern-financial' blocks create on 'financial_record'
    #  for principal 'intern-001'"
```

---

## Models

### ScopedObject

```python
@dataclass(frozen=True)
class ScopedObject:
    id: str
    object_type: str
    owner_id: str
    data: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    version_number: int
    lifecycle: str
    metadata: dict[str, Any]
```

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique object identifier (UUID v4). |
| `object_type` | `str` | Classification string. |
| `owner_id` | `str` | ID of the owning principal. |
| `data` | `dict[str, Any]` | Current version's payload. |
| `created_at` | `datetime` | UTC timestamp when the object was first created. |
| `updated_at` | `datetime` | UTC timestamp of the most recent version. |
| `version_number` | `int` | Current (latest) version number. |
| `lifecycle` | `str` | One of `"active"`, `"deleted"`. |
| `metadata` | `dict[str, Any]` | System and user metadata. |

### ObjectVersion

```python
@dataclass(frozen=True)
class ObjectVersion:
    id: str
    object_id: str
    version_number: int
    data: dict[str, Any]
    created_at: datetime
    created_by: str
    change_reason: str | None
    checksum: str
```

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique version identifier. |
| `object_id` | `str` | Parent object ID. |
| `version_number` | `int` | Sequential version number starting at 1. |
| `data` | `dict[str, Any]` | The immutable snapshot of the object's data at this version. |
| `created_at` | `datetime` | UTC timestamp of version creation. |
| `created_by` | `str` | Principal ID that created this version. |
| `change_reason` | `str \| None` | Human-readable reason provided at creation or update time. |
| `checksum` | `str` | SHA-256 checksum of the serialized data, for integrity verification. |

### Tombstone

```python
@dataclass(frozen=True)
class Tombstone:
    id: str
    object_id: str
    deleted_at: datetime
    deleted_by: str
    reason: str | None
    last_version_number: int
```

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique tombstone identifier. |
| `object_id` | `str` | The soft-deleted object's ID. |
| `deleted_at` | `datetime` | UTC timestamp of deletion. |
| `deleted_by` | `str` | Principal ID that performed the deletion. |
| `reason` | `str \| None` | Reason for deletion. |
| `last_version_number` | `int` | The version number at the time of deletion. |

---

## Typed Object Protocol

Register a type for automatic serialization/deserialization of versioned data:

```python
from pydantic import BaseModel
import scoped

class Invoice(BaseModel):
    amount: float
    currency: str
    status: str = "draft"

scoped.register_type("invoice", Invoice)

# Create with a typed instance — auto-serialized to dict
doc, v1 = scoped.objects.create("invoice", data=Invoice(amount=500, currency="USD"))

# Read with typed access
versions = scoped.objects.versions(doc.id)
invoice = versions[0].typed_data  # Invoice(amount=500, currency="USD", status="draft")

# Dict path still works (backward compatible)
doc, v2 = scoped.objects.create("invoice", data={"amount": 500, "currency": "USD"})
```

### Supported Types

| Type | Adapter | Serialize | Deserialize |
|---|---|---|---|
| Pydantic `BaseModel` | `PydanticAdapter` | `model_dump(mode="json")` | `model_validate(data)` |
| `@dataclass` | `DataclassAdapter` | `dataclasses.asdict()` | `cls(**data)` |
| `ScopedSerializable` | `ScopedSerializableAdapter` | `to_scoped_dict()` | `from_scoped_dict(data)` |

Types are auto-detected when registered. The type registry is thread-safe.

### Custom Protocol

```python
from scoped.types import ScopedSerializable

class MyType:
    def to_scoped_dict(self) -> dict[str, Any]:
        return {"key": self.key, "value": self.value}

    @classmethod
    def from_scoped_dict(cls, data: dict[str, Any]) -> "MyType":
        return cls(key=data["key"], value=data["value"])

scoped.register_type("my_type", MyType)
```

---

## Complete Example

```python
from scoped.client import ScopedClient

with ScopedClient(database_url="sqlite:///app.db") as client:
    admin = client.principals.create(display_name="Admin", kind="user")

    with client.as_principal(admin):
        # Create
        doc, v1 = client.objects.create(
            object_type="document",
            data={"title": "Architecture Overview", "status": "draft"},
            change_reason="Initial creation",
        )

        # Batch create
        configs = client.objects.create_many(
            items=[
                {"object_type": "config", "data": {"key": "timeout", "value": 30}},
                {"object_type": "config", "data": {"key": "retries", "value": 5}},
            ],
        )

        # Update (creates v2)
        doc, v2 = client.objects.update(
            doc.id,
            data={"title": "Architecture Overview", "status": "published"},
            change_reason="Published after review",
        )
        assert v2.version_number == 2

        # List with filtering
        docs = client.objects.list(object_type="document", order_by="-created_at")
        print(f"Found {len(docs)} document(s)")

        # Version history
        versions = client.objects.versions(doc.id)
        for v in versions:
            print(f"  v{v.version_number}: {v.change_reason}")

        # Soft-delete
        tombstone = client.objects.delete(doc.id, reason="No longer needed")
        assert client.objects.get(doc.id) is None
```
