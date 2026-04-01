---
title: "Audit Trail API"
description: "API reference for AuditNamespace -- querying, filtering, and verifying the append-only, hash-chained audit trail."
category: "API Reference"
---

# Audit Trail API

The `AuditNamespace` provides read-only access to pyscoped's append-only, hash-chained
audit trail. Every state-changing operation in the system -- object creation, scope
membership changes, secret rotation, and more -- is recorded as a `TraceEntry` with a
cryptographic hash linking it to the previous entry.

Access the namespace through the client:

```python
from pyscoped import ScopedClient

client = ScopedClient(database_url="sqlite:///app.db")
audit = client.audit
```

---

## Methods

### for_object

```python
audit.for_object(
    object_id: str,
    limit: int = 100,
) -> list[TraceEntry]
```

Returns audit entries related to a specific object, ordered by sequence number
descending (newest first).

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `object_id` | `str` | *required* | The object ID to filter by. |
| `limit` | `int` | `100` | Maximum number of entries to return. |

#### Returns

A list of `TraceEntry` instances.

#### Example

```python
entries = client.audit.for_object(doc.id)
for entry in entries:
    print(f"[{entry.timestamp}] {entry.action} by {entry.actor_id}")
# [2026-03-31 14:22:01] object.update by alice-001
# [2026-03-31 14:20:55] object.create by alice-001
```

---

### for_principal

```python
audit.for_principal(
    principal_id: str,
    limit: int = 100,
) -> list[TraceEntry]
```

Returns audit entries where the given principal was the actor, ordered by sequence
number descending.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `principal_id` | `str` | *required* | The principal ID to filter by. |
| `limit` | `int` | `100` | Maximum number of entries to return. |

#### Returns

A list of `TraceEntry` instances.

#### Example

```python
alice_actions = client.audit.for_principal(alice.id, limit=5)
for entry in alice_actions:
    print(f"{entry.action} -> {entry.target_type}:{entry.target_id}")
```

---

### for_scope

```python
audit.for_scope(
    scope_id: str,
    limit: int = 100,
) -> list[TraceEntry]
```

Returns audit entries associated with a specific scope, including membership changes,
projections, and scope lifecycle events.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `scope_id` | `str` | *required* | The scope ID to filter by. |
| `limit` | `int` | `100` | Maximum number of entries to return. |

#### Returns

A list of `TraceEntry` instances.

#### Example

```python
scope_trail = client.audit.for_scope(eng_scope.id)
for entry in scope_trail:
    print(f"{entry.action}: {entry.metadata}")
```

---

### query

```python
audit.query(
    actor_id: str | None = None,
    action: str | ActionType | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    scope_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    order_by: str = "-sequence",
    limit: int = 100,
    offset: int = 0,
) -> list[TraceEntry]
```

General-purpose audit query with full filtering and pagination. All filter parameters
are optional and combined with AND logic.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `actor_id` | `str \| None` | `None` | Filter to entries by this actor. |
| `action` | `str \| ActionType \| None` | `None` | Filter by action type. Accepts a string (e.g. `"object.create"`) or an `ActionType` enum value. |
| `target_type` | `str \| None` | `None` | Filter by target entity type (e.g. `"object"`, `"scope"`, `"principal"`, `"secret"`). |
| `target_id` | `str \| None` | `None` | Filter to entries affecting this specific target. |
| `scope_id` | `str \| None` | `None` | Filter to entries associated with this scope. |
| `since` | `datetime \| None` | `None` | Only entries at or after this UTC timestamp. |
| `until` | `datetime \| None` | `None` | Only entries before this UTC timestamp. |
| `order_by` | `str` | `"-sequence"` | Sort order. See [order_by values](#order_by-values). |
| `limit` | `int` | `100` | Maximum number of entries to return. |
| `offset` | `int` | `0` | Number of entries to skip. |

#### Returns

A list of `TraceEntry` instances matching all provided filters.

#### Example

```python
from datetime import datetime, timedelta, timezone
from pyscoped.audit import ActionType

# All object creations in the last 24 hours
recent_creates = client.audit.query(
    action=ActionType.OBJECT_CREATE,
    since=datetime.now(timezone.utc) - timedelta(hours=24),
    order_by="-timestamp",
    limit=50,
)

# Everything Alice did in the engineering scope
alice_in_eng = client.audit.query(
    actor_id=alice.id,
    scope_id=eng_scope.id,
)

# Paginated full trail
page1 = client.audit.query(limit=100, offset=0)
page2 = client.audit.query(limit=100, offset=100)

# Combined filters
secret_rotations = client.audit.query(
    action="secret.rotate",
    target_type="secret",
    since=datetime(2026, 1, 1, tzinfo=timezone.utc),
    until=datetime(2026, 4, 1, tzinfo=timezone.utc),
)
```

---

### verify

```python
audit.verify(
    from_sequence: int | None = None,
    to_sequence: int | None = None,
) -> ChainVerification
```

Verifies the integrity of the hash chain in the audit trail. Each entry's `hash`
field is computed from its contents and the `previous_hash`, forming a tamper-evident
chain. This method walks the chain and confirms that every hash is correct.

Verification is performed in bounded-memory chunks, making it safe to run against
large audit trails.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `from_sequence` | `int \| None` | `None` | Start verification at this sequence number. `None` starts from the first entry. |
| `to_sequence` | `int \| None` | `None` | End verification at this sequence number. `None` continues to the latest entry. |

#### Returns

A `ChainVerification` model instance.

#### Raises

| Exception | Condition |
|---|---|
| `SequenceRangeError` | `from_sequence` is greater than `to_sequence`, or either value is negative. |

#### Example

```python
# Verify the entire chain
result = client.audit.verify()
print(result.valid)             # True
print(result.entries_checked)   # 1842
print(result.first_sequence)   # 1
print(result.last_sequence)    # 1842

# Verify a specific range
partial = client.audit.verify(from_sequence=1000, to_sequence=1500)
print(partial.valid)            # True
print(partial.entries_checked)  # 501

# Detect tampering
tampered = client.audit.verify()
if not tampered.valid:
    print(f"Chain broken at sequence {tampered.broken_at_sequence}")
```

---

## order_by Values

| Value | Description |
|---|---|
| `"sequence"` | Ascending by sequence number (oldest first). |
| `"-sequence"` | Descending by sequence number (newest first). Default. |
| `"timestamp"` | Ascending by timestamp. |
| `"-timestamp"` | Descending by timestamp. |

---

## Models

### TraceEntry

The fundamental audit record. Every state-changing operation produces exactly one
trace entry.

```python
@dataclass(frozen=True)
class TraceEntry:
    id: str
    sequence: int
    actor_id: str
    action: str
    target_type: str
    target_id: str
    timestamp: datetime
    hash: str
    previous_hash: str | None
    scope_id: str | None
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    metadata: dict[str, Any]
```

#### Fields

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique entry identifier (UUID v4). |
| `sequence` | `int` | Monotonically increasing sequence number. Guaranteed to be gap-free. |
| `actor_id` | `str` | ID of the principal that performed the action. |
| `action` | `str` | The action type string (e.g. `"object.create"`). See [ActionType Enum](#actiontype-enum). |
| `target_type` | `str` | The type of entity affected: `"object"`, `"principal"`, `"scope"`, `"secret"`, `"scope_membership"`, `"scope_projection"`. |
| `target_id` | `str` | ID of the affected entity. |
| `timestamp` | `datetime` | UTC timestamp of the operation. |
| `hash` | `str` | SHA-256 hash of the entry contents and `previous_hash`, forming the chain. |
| `previous_hash` | `str \| None` | Hash of the preceding entry. `None` for the first entry in the chain. |
| `scope_id` | `str \| None` | Scope associated with the operation, if applicable. |
| `before_state` | `dict[str, Any] \| None` | Snapshot of the target entity before the operation. `None` for create operations. |
| `after_state` | `dict[str, Any] \| None` | Snapshot of the target entity after the operation. `None` for delete operations. |
| `metadata` | `dict[str, Any]` | Additional context: `change_reason`, IP address, request ID, etc. |

---

### ChainVerification

Result of an audit chain integrity check.

```python
@dataclass(frozen=True)
class ChainVerification:
    valid: bool
    entries_checked: int
    first_sequence: int | None
    last_sequence: int | None
    broken_at_sequence: int | None
```

#### Fields

| Field | Type | Description |
|---|---|---|
| `valid` | `bool` | `True` if every hash in the range is correct. |
| `entries_checked` | `int` | Number of entries that were verified. |
| `first_sequence` | `int \| None` | Sequence number of the first verified entry. `None` if the trail is empty. |
| `last_sequence` | `int \| None` | Sequence number of the last verified entry. `None` if the trail is empty. |
| `broken_at_sequence` | `int \| None` | Sequence number where the chain is broken. `None` if the chain is valid. |

---

## ActionType Enum

The `ActionType` enum defines all recognized audit actions.

```python
from pyscoped.audit import ActionType
```

| Enum Value | String | Description |
|---|---|---|
| `ActionType.PRINCIPAL_CREATE` | `"principal.create"` | A principal was created. |
| `ActionType.PRINCIPAL_UPDATE` | `"principal.update"` | A principal was updated. |
| `ActionType.PRINCIPAL_SUSPEND` | `"principal.suspend"` | A principal was suspended. |
| `ActionType.PRINCIPAL_ARCHIVE` | `"principal.archive"` | A principal was archived. |
| `ActionType.OBJECT_CREATE` | `"object.create"` | An object was created. |
| `ActionType.OBJECT_UPDATE` | `"object.update"` | An object was updated (new version). |
| `ActionType.OBJECT_DELETE` | `"object.delete"` | An object was soft-deleted. |
| `ActionType.SCOPE_CREATE` | `"scope.create"` | A scope was created. |
| `ActionType.SCOPE_UPDATE` | `"scope.update"` | A scope was updated. |
| `ActionType.SCOPE_RENAME` | `"scope.rename"` | A scope was renamed. |
| `ActionType.SCOPE_FREEZE` | `"scope.freeze"` | A scope was frozen. |
| `ActionType.SCOPE_ARCHIVE` | `"scope.archive"` | A scope was archived. |
| `ActionType.SCOPE_ADD_MEMBER` | `"scope.add_member"` | A member was added to a scope. |
| `ActionType.SCOPE_REMOVE_MEMBER` | `"scope.remove_member"` | A member was removed from a scope. |
| `ActionType.SCOPE_PROJECT` | `"scope.project"` | An object was projected into a scope. |
| `ActionType.SCOPE_UNPROJECT` | `"scope.unproject"` | An object projection was removed. |
| `ActionType.SECRET_CREATE` | `"secret.create"` | A secret was created. |
| `ActionType.SECRET_ROTATE` | `"secret.rotate"` | A secret was rotated. |
| `ActionType.SECRET_GRANT` | `"secret.grant"` | A secret reference was granted. |
| `ActionType.SECRET_RESOLVE` | `"secret.resolve"` | A secret reference was resolved (value accessed). |
| `ActionType.SECRET_REVOKE` | `"secret.revoke"` | A secret reference was revoked. |
| `ActionType.SYNC_START` | `"sync.start"` | Background sync was started. |
| `ActionType.SYNC_COMPLETE` | `"sync.complete"` | A sync cycle completed. |
| `ActionType.SYNC_CONFLICT` | `"sync.conflict"` | A sync conflict was detected and resolved. |

---

## Complete Example

```python
from datetime import datetime, timedelta, timezone
from pyscoped import ScopedClient
from pyscoped.audit import ActionType

with ScopedClient(database_url="sqlite:///app.db") as client:
    # Set up some data to generate audit entries
    alice = client.principals.create(display_name="Alice", kind="user")
    with client.as_principal(alice):
        scope = client.scopes.create(name="project-x")
        client.scopes.add_member(scope.id, alice.id, role="owner")
        doc, v1 = client.objects.create(
            object_type="spec",
            data={"title": "Design"},
            change_reason="Initial draft",
        )
        doc, v2 = client.objects.update(
            doc.id,
            data={"title": "Design (Final)"},
            change_reason="Finalized",
        )

    # Query the full trail for the object
    obj_trail = client.audit.for_object(doc.id)
    print(f"{len(obj_trail)} entries for {doc.id}")
    for entry in obj_trail:
        print(f"  seq={entry.sequence} {entry.action} by {entry.actor_id}")
        if entry.before_state:
            print(f"    before: {entry.before_state}")
        if entry.after_state:
            print(f"    after:  {entry.after_state}")

    # Query by principal
    alice_trail = client.audit.for_principal(alice.id, limit=10)
    print(f"\nAlice performed {len(alice_trail)} actions")

    # Query by scope
    scope_trail = client.audit.for_scope(scope.id)
    print(f"\nScope trail: {len(scope_trail)} entries")

    # Advanced query: all creates in the last hour
    recent = client.audit.query(
        action=ActionType.OBJECT_CREATE,
        since=datetime.now(timezone.utc) - timedelta(hours=1),
        order_by="-timestamp",
    )

    # Verify chain integrity
    verification = client.audit.verify()
    if verification.valid:
        print(f"\nAudit chain valid: {verification.entries_checked} entries checked")
    else:
        print(f"\nAudit chain BROKEN at sequence {verification.broken_at_sequence}")
```
