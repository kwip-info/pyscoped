---
title: "Events & Webhooks"
description: "Emit domain events, register listeners, deliver webhooks with retry logic, and route in-app notifications through the pyscoped event system."
category: "Extensions"
---

# Events & Webhooks

pyscoped ships with a full event pipeline: emit structured domain events, fan them
out to in-process listeners, deliver them to external HTTP endpoints via webhooks,
and generate in-app notifications -- all wired to the same `EventBus`.

## EventBus

The `EventBus` is the central hub for all domain events. Every state change in
pyscoped -- object creation, scope modification, membership change -- can flow
through the bus.

```python
from scoped.events import EventBus, EventType

bus = EventBus(backend=storage)

# Emit an event
event = bus.emit(
    event_type=EventType.OBJECT_CREATED,
    actor_id="user-42",
    target_type="Document",
    target_id="doc-99",
    scope_id="scope-1",
    data={"title": "Q4 Report"},
)
```

### Registering listeners

Listeners are plain callables. They receive the full event dict and run
synchronously in the order they were registered.

```python
def on_object_created(event):
    print(f"{event['actor_id']} created {event['target_id']}")

bus.on(EventType.OBJECT_CREATED, on_object_created)

# Remove a specific listener
bus.off(EventType.OBJECT_CREATED, on_object_created)
```

### Querying events

```python
# All events in a scope
events = bus.list_events(scope_id="scope-1")

# Filtered by type
events = bus.list_events(
    scope_id="scope-1",
    event_type=EventType.OBJECT_UPDATED,
)

# Count without fetching
total = bus.count_events(scope_id="scope-1")
```

## EventType enum

The `EventType` enum covers every domain action recognised by pyscoped:

| Category | Members |
|---|---|
| Object lifecycle | `OBJECT_CREATED`, `OBJECT_UPDATED`, `OBJECT_DELETED` |
| Scope lifecycle | `SCOPE_CREATED`, `SCOPE_MODIFIED`, `SCOPE_DISSOLVED` |
| Membership | `MEMBERSHIP_CHANGED` |
| Rules | `RULE_CHANGED` |
| Environments | `ENVIRONMENT_CREATED`, `ENVIRONMENT_UPDATED`, `ENVIRONMENT_ARCHIVED` |
| Deployments | `DEPLOYMENT_STARTED`, `DEPLOYMENT_COMPLETED`, `DEPLOYMENT_FAILED`, `DEPLOYMENT_ROLLED_BACK` |
| Secrets | `SECRET_CREATED`, `SECRET_ROTATED`, `SECRET_EXPIRED`, `SECRET_LEAK_DETECTED` |
| Connectors | `CONNECTOR_PROPOSED`, `CONNECTOR_APPROVED`, `CONNECTOR_REVOKED` |
| Custom | `CUSTOM` |

Use `EventType.CUSTOM` for application-specific events. The `data` dict on the
event carries any extra payload your application needs.

## SubscriptionManager

The `SubscriptionManager` ties webhook endpoints to event subscriptions. An
endpoint is "where to deliver"; a subscription is "which events go there".

```python
from scoped.events import SubscriptionManager

sm = SubscriptionManager(backend=storage)

# 1. Create a webhook endpoint
webhook = sm.create_webhook(
    name="Slack notifier",
    url="https://hooks.slack.example.com/ingest",
    owner_id="user-42",
)

# 2. Subscribe that endpoint to specific event types
sub = sm.create_subscription(
    name="Object changes",
    owner_id="user-42",
    event_types=[
        EventType.OBJECT_CREATED,
        EventType.OBJECT_UPDATED,
        EventType.OBJECT_DELETED,
    ],
    webhook_endpoint_id=webhook["id"],
)

# Tear down
sm.delete_subscription(sub["id"])
sm.delete_webhook(webhook["id"])
```

## WebhookDelivery

`WebhookDelivery` manages the outbound HTTP lifecycle of webhook payloads,
including pending delivery, failure tracking, and retry with exponential backoff.

### Delivering pending events

```python
from scoped.events import WebhookDelivery

wd = WebhookDelivery(backend=storage)

# Attempt delivery for all PENDING items
results = wd.deliver_pending()

# Inspect delivery records
deliveries = wd.get_deliveries(webhook_endpoint_id=webhook["id"])
for d in deliveries:
    print(d["status"], d["attempts"], d["last_error"])
```

### DeliveryStatus

Every delivery record carries one of these states:

| Status | Meaning |
|---|---|
| `PENDING` | Queued, not yet attempted |
| `DELIVERED` | Successfully sent (HTTP 2xx) |
| `FAILED` | Permanently failed after max retries |
| `RETRYING` | Failed once or more, awaiting next attempt |

### Retry with exponential backoff

`retry_failed` picks up all deliveries in `RETRYING` state and re-attempts
them. The delay between attempts follows an exponential backoff formula:

```
delay = backoff_base * 2^(attempt - 1)   # seconds
```

With the default `backoff_base=60`, attempts are spaced at 60 s, 120 s, 240 s,
480 s, and so on.

```python
wd.retry_failed(backoff_base=60)
```

### Pluggable HTTP transport

By default, `WebhookDelivery` uses its built-in `http_transport` static method,
which makes a synchronous `POST` request with the JSON-serialised event body.
You can replace it with any callable that matches the same signature:

```python
def custom_transport(url: str, payload: dict, headers: dict) -> int:
    """Return an HTTP status code."""
    resp = my_async_client.post(url, json=payload, headers=headers)
    return resp.status_code

wd = WebhookDelivery(backend=storage, transport=custom_transport)
```

The built-in transport is available as `WebhookDelivery.http_transport` for
reference or delegation.

## NotificationEngine

The `NotificationEngine` generates in-app notifications from events by
evaluating notification rules. Rules match on event type and produce a
formatted message using simple template strings.

### Creating notification rules

```python
from scoped.events import NotificationEngine

ne = NotificationEngine(backend=storage)

rule = ne.create_rule(
    name="Object change alert",
    owner_id="user-42",
    event_types=[EventType.OBJECT_CREATED, EventType.OBJECT_UPDATED],
    template="{actor_id} {event_type} {target_type} '{data[title]}'",
    recipient_ids=["user-42", "user-99"],
)
```

### Processing events into notifications

Call `process_event` after emitting an event (or from a bus listener) to
evaluate all matching rules and persist notifications.

```python
ne.process_event(event)
```

### Querying and managing notifications

```python
# List notifications for a user
notes = ne.list_notifications(recipient_id="user-42")

# Unread count
count = ne.count_unread(recipient_id="user-42")

# Mark individual notifications
ne.mark_read(notification_id=notes[0]["id"])
ne.mark_dismissed(notification_id=notes[1]["id"])
```

### Template formatting

Templates use Python `str.format_map` syntax. The full event dict is available
as the format context, so you can reference any key:

```
"{actor_id} performed {event_type} on {target_type} {target_id}"
```

Nested `data` fields are accessible with bracket notation:

```
"New document: {data[title]} in scope {scope_id}"
```

## Putting it all together

A complete example that wires the event bus to both webhooks and notifications:

```python
from scoped import Client
from scoped.events import (
    EventBus,
    EventType,
    NotificationEngine,
    SubscriptionManager,
    WebhookDelivery,
)

client = Client()
storage = client.storage

bus = EventBus(backend=storage)
sm = SubscriptionManager(backend=storage)
wd = WebhookDelivery(backend=storage)
ne = NotificationEngine(backend=storage)

# Set up webhook pipeline
endpoint = sm.create_webhook(
    name="Audit sink",
    url="https://audit.internal/events",
    owner_id="system",
)
sm.create_subscription(
    name="All object events",
    owner_id="system",
    event_types=[
        EventType.OBJECT_CREATED,
        EventType.OBJECT_UPDATED,
        EventType.OBJECT_DELETED,
    ],
    webhook_endpoint_id=endpoint["id"],
)

# Set up in-app notification rule
ne.create_rule(
    name="Deletion alert",
    owner_id="system",
    event_types=[EventType.OBJECT_DELETED],
    template="Warning: {actor_id} deleted {target_type} {target_id}",
    recipient_ids=["admin-1"],
)

# Register a bus listener that drives both pipelines
def fan_out(event):
    wd.deliver_pending()
    ne.process_event(event)

bus.on(EventType.OBJECT_CREATED, fan_out)
bus.on(EventType.OBJECT_UPDATED, fan_out)
bus.on(EventType.OBJECT_DELETED, fan_out)

# Now emit -- listener handles the rest
bus.emit(
    event_type=EventType.OBJECT_DELETED,
    actor_id="user-7",
    target_type="Document",
    target_id="doc-55",
    scope_id="scope-1",
    data={},
)

# Later: retry any failed deliveries
wd.retry_failed(backoff_base=60)
```
