---
title: "Connectors & Federation"
description: "Establish governed cross-scope data channels, enforce connector policies, synchronise objects, and exchange federation messages with pyscoped."
category: "Extensions"
---

# Connectors & Federation

Connectors let two scopes exchange objects through a governed, auditable channel.
Every connector follows a strict state machine, carries directional metadata,
and enforces pluggable policies before any data crosses the boundary.

The federation layer extends connectors with a signed message protocol for
inter-instance communication.

## ConnectorManager

`ConnectorManager` is the entry point for the entire connector lifecycle.

```python
from scoped.connectors import ConnectorManager

cm = ConnectorManager(backend=storage)
```

### Lifecycle methods

| Method | Transition | Description |
|---|---|---|
| `propose()` | -- -> `PROPOSED` | Draft a new connector |
| `submit_for_approval()` | `PROPOSED` -> `PENDING_APPROVAL` | Send to approvers |
| `approve()` | `PENDING_APPROVAL` -> `ACTIVE` | Activate the connector |
| `reject()` | `PENDING_APPROVAL` -> `REJECTED` | Permanently reject |
| `suspend()` | `ACTIVE` -> `SUSPENDED` | Temporarily disable |
| `reactivate()` | `SUSPENDED` -> `ACTIVE` | Re-enable a suspended connector |
| `revoke()` | any non-terminal -> `REVOKED` | Permanently disable |

### State machine

```
PROPOSED -> PENDING_APPROVAL -> ACTIVE <-> SUSPENDED
                  |                           |
                  v                           |
              REJECTED          REVOKED <-----+---- (from any non-terminal)
```

Terminal states (`REJECTED`, `REVOKED`) cannot be left. Attempting an invalid
transition raises `ConnectorError`.

### Creating a connector

```python
connector = cm.propose(
    name="CRM to Billing sync",
    owner_id="user-42",
    source_scope_id="scope-crm",
    target_scope_id="scope-billing",
    direction="OUTBOUND",
    description="Push customer records from CRM to Billing nightly.",
)

cm.submit_for_approval(connector["id"], submitted_by="user-42")
cm.approve(connector["id"], approved_by="admin-1")
```

## ConnectorDirection

Each connector declares its data-flow direction:

| Direction | Meaning |
|---|---|
| `INBOUND` | Data flows into the connector owner's scope |
| `OUTBOUND` | Data flows out of the connector owner's scope |
| `BIDIRECTIONAL` | Data flows in both directions |

The direction is checked at sync time. Attempting to push data through an
`INBOUND`-only connector raises `ConnectorPolicyViolation`.

## Policies

Policies are guardrails attached to a connector. They are evaluated before
every sync operation.

### Adding a policy

```python
from scoped.connectors import PolicyType

cm.add_policy(
    connector_id=connector["id"],
    policy_type=PolicyType.ALLOW_TYPES,
    config={"types": ["Customer", "Invoice"]},
)

cm.add_policy(
    connector_id=connector["id"],
    policy_type=PolicyType.DENY_TYPES,
    config={"types": ["InternalNote"]},
)

cm.add_policy(
    connector_id=connector["id"],
    policy_type=PolicyType.RATE_LIMIT,
    config={"max_per_minute": 100},
)

cm.add_policy(
    connector_id=connector["id"],
    policy_type=PolicyType.CLASSIFICATION,
    config={"max_classification": "internal"},
)
```

### PolicyType

| Type | Behaviour |
|---|---|
| `ALLOW_TYPES` | Only listed object types may pass |
| `DENY_TYPES` | Listed object types are blocked |
| `RATE_LIMIT` | Caps throughput per time window |
| `CLASSIFICATION` | Blocks objects above a sensitivity threshold |

### Checking policies

Before syncing, you can check whether a given object type is permitted:

```python
allowed = cm.check_policy(
    connector_id=connector["id"],
    object_type="Customer",
)
# True / False
```

### Hard rule: secrets never flow through connectors

Regardless of policy configuration, pyscoped enforces a hard rule: objects of
type `Secret` (or any type managed by the secrets subsystem) are **never**
permitted through a connector. Attempting to sync a secret raises
`SecretAccessDeniedError` before any policy evaluation takes place.

## Syncing objects

`sync_object` is the primary data-transfer method. It validates state, checks
direction and policies, pushes the payload via the configured transport, and
records a traffic entry.

```python
result = cm.sync_object(
    connector_id=connector["id"],
    object_type="Customer",
    object_id="cust-123",
    payload={"name": "Acme Corp", "tier": "enterprise"},
    synced_by="user-42",
)
# result == {"status": "synced", "traffic_id": "..."}
```

### Validation order

1. Connector must be in `ACTIVE` state.
2. Direction must permit the operation (outbound push / inbound pull).
3. All attached policies are evaluated. Any violation short-circuits with
   `ConnectorPolicyViolation`.
4. The transport callable is invoked with the payload.
5. A traffic record is persisted for auditing.

### Pluggable transport

By default, `ConnectorManager` uses its built-in `http_transport` static
method to POST the payload to the target scope's sync endpoint. You can
replace it with any callable matching the signature:

```python
def custom_transport(
    connector: dict,
    object_type: str,
    object_id: str,
    payload: dict,
) -> dict:
    """Push payload and return a result dict."""
    # e.g., write to a message queue instead of HTTP
    queue.send(payload)
    return {"status": "queued"}

cm = ConnectorManager(backend=storage, transport=custom_transport)
```

The built-in transport is available as `ConnectorManager.http_transport`.

## Traffic recording and querying

Every successful sync creates a traffic record. Query them for auditing,
debugging, or billing:

```python
traffic = cm.get_traffic(connector_id=connector["id"])
for entry in traffic:
    print(
        entry["timestamp"],
        entry["object_type"],
        entry["object_id"],
        entry["synced_by"],
    )

# Filter by time range
from datetime import datetime, timezone, timedelta

since = datetime.now(timezone.utc) - timedelta(hours=24)
recent = cm.get_traffic(connector_id=connector["id"], since=since)
```

## FederationProtocol

The `FederationProtocol` extends connectors with a signed-message layer for
communication between separate pyscoped instances (or compatible third-party
systems).

```python
from scoped.connectors import FederationProtocol

fp = FederationProtocol(backend=storage, signing_key=private_key)
```

### Creating and verifying messages

```python
# Sender side
message = fp.create_message(
    connector_id=connector["id"],
    message_type="sync_request",
    payload={"object_type": "Customer", "object_id": "cust-123"},
)
# message contains: id, signature, timestamp, payload

# Receiver side
valid = fp.verify_message(message, public_key=sender_public_key)
if not valid:
    raise FederationError("Signature verification failed")
```

### Schema negotiation

Before two instances begin exchanging data, they can negotiate a shared schema
to ensure field-level compatibility.

```python
schema_agreement = fp.negotiate_schema(
    connector_id=connector["id"],
    proposed_schema={
        "Customer": {
            "fields": ["id", "name", "email", "tier"],
            "version": 2,
        }
    },
)
# Returns the agreed-upon schema or raises FederationError
```

## End-to-end example

```python
from scoped.client import ScopedClient
from scoped.connectors import ConnectorManager, FederationProtocol, PolicyType

client = ScopedClient()
storage = client.storage
cm = ConnectorManager(backend=storage)

# 1. Propose and activate
conn = cm.propose(
    name="Warehouse feed",
    owner_id="logistics-team",
    source_scope_id="scope-orders",
    target_scope_id="scope-warehouse",
    direction="OUTBOUND",
)
cm.submit_for_approval(conn["id"], submitted_by="logistics-team")
cm.approve(conn["id"], approved_by="admin-1")

# 2. Attach policies
cm.add_policy(conn["id"], PolicyType.ALLOW_TYPES, {"types": ["Order", "Shipment"]})
cm.add_policy(conn["id"], PolicyType.RATE_LIMIT, {"max_per_minute": 200})

# 3. Sync an object
cm.sync_object(
    connector_id=conn["id"],
    object_type="Order",
    object_id="ord-456",
    payload={"items": 3, "total": 149.99},
    synced_by="logistics-team",
)

# 4. Audit traffic
for t in cm.get_traffic(connector_id=conn["id"]):
    print(t["timestamp"], t["object_type"], t["object_id"])

# 5. Suspend if needed
cm.suspend(conn["id"])
```
