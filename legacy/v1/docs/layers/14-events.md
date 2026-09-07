# Layer 14: Events & Webhooks

## Purpose

The audit trail (Layer 6) is a passive, immutable record. The hook system (Layer 12) is synchronous and plugin-scoped. Events are the **asynchronous, scoped event bus** — the reactive counterpart to audit. When something happens in Scoped, events let the rest of the system (and the outside world) react.

## Dependencies

- **Layer 5 (Rules)** — event subscriptions and webhook delivery are governed by rules
- **Layer 6 (Audit)** — events can reference source trace entries
- **Layer 12 (Integrations)** — webhook endpoints are registered integrations

## Core Concepts

### Event

A typed, scoped occurrence. Events are emitted when significant actions happen — object creation, scope modification, deployment completion, etc.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `event_type` | What happened (`EventType` enum) |
| `actor_id` | Who triggered it |
| `target_type` | What kind of thing was affected |
| `target_id` | Which specific thing was affected |
| `timestamp` | When it happened |
| `scope_id` | Optional scope context |
| `data` | Arbitrary payload (JSON-serializable) |
| `source_trace_id` | Optional link back to the audit trail entry |
| `lifecycle` | Active/archived state |

### Event Types

The framework defines 18 built-in event types covering all major actions:

- **Object lifecycle:** `OBJECT_CREATED`, `OBJECT_UPDATED`, `OBJECT_DELETED`
- **Scope lifecycle:** `SCOPE_CREATED`, `SCOPE_MODIFIED`, `SCOPE_DISSOLVED`
- **Membership:** `MEMBERSHIP_CHANGED`
- **Rules:** `RULE_CHANGED`
- **Environments:** `ENVIRONMENT_SPAWNED`, `ENVIRONMENT_COMPLETED`, `ENVIRONMENT_DISCARDED`, `ENVIRONMENT_PROMOTED`
- **Deployments:** `DEPLOYMENT_COMPLETED`, `DEPLOYMENT_ROLLED_BACK`
- **Secrets:** `SECRET_ROTATED`
- **Flow:** `STAGE_TRANSITIONED`
- **Connectors:** `CONNECTOR_SYNCED`
- **Custom:** `CUSTOM` — application-defined event types

### EventSubscription

A principal or scope subscribes to event patterns with filters. Subscriptions match events by event type, target type, and scope.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `name` | Human-readable label |
| `owner_id` | Who created this subscription |
| `event_types` | Filter: which event types to match |
| `target_types` | Filter: which target types to match |
| `scope_id` | Filter: only events in this scope |
| `webhook_endpoint_id` | Optional: deliver matches to this webhook |

Subscriptions respect scope visibility — you only receive events you're authorized to see.

### WebhookEndpoint

An outbound HTTP delivery target for events. Registered with a URL, optional configuration, and an owner.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `name` | Human-readable label |
| `owner_id` | Who owns this endpoint |
| `url` | Where to deliver events |
| `config` | Headers, auth, format options |
| `scope_id` | Optional scope binding |

### DeliveryAttempt

A record of an attempt to deliver an event to a webhook endpoint.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `event_id` | Which event was being delivered |
| `webhook_endpoint_id` | Which endpoint received it |
| `subscription_id` | Which subscription triggered it |
| `status` | `PENDING`, `DELIVERED`, `FAILED`, `RETRYING` |
| `attempt_number` | Retry count |
| `response_status` | HTTP status code from the endpoint |
| `error_message` | Error details on failure |

## Architecture

### EventBus

The central dispatch mechanism. When an event is emitted:

1. The event is **persisted** to storage
2. Active subscriptions are **matched** against the event
3. Matching subscriptions with webhooks get **delivery records** queued
4. In-process **listeners** are notified synchronously

```python
bus = EventBus(backend)

# Register in-process listener
bus.on(EventType.OBJECT_CREATED, my_handler)

# Emit an event
event = bus.emit(
    EventType.OBJECT_CREATED,
    actor_id=user.id,
    target_type="document",
    target_id=doc.id,
    scope_id=scope.id,
    data={"title": "New Report"},
)

# Query events
events = bus.list_events(event_type=EventType.OBJECT_CREATED, scope_id=scope.id)
```

### SubscriptionManager

CRUD operations for webhook endpoints and event subscriptions. Enforces owner-only deletion with `AccessDeniedError`.

```python
subs = SubscriptionManager(backend)

# Create a webhook endpoint
webhook = subs.create_webhook(
    name="Slack Notify",
    url="https://hooks.slack.com/...",
    owner_id=user.id,
)

# Subscribe to events
subscription = subs.create_subscription(
    name="Doc Changes",
    owner_id=user.id,
    event_types=["object_created", "object_updated"],
    target_types=["document"],
    webhook_endpoint_id=webhook.id,
)
```

### WebhookDelivery

Manages outbound HTTP delivery with retry logic. Uses a pluggable `DeliveryTransport` callable for testability.

```python
delivery = WebhookDelivery(backend, transport=http_post, max_retries=3)

# Deliver all pending webhooks
attempts = delivery.deliver_pending()

# Retry failed deliveries
retries = delivery.retry_failed()
```

## Key Files

```
scoped/events/
    __init__.py          # Package exports
    models.py            # Event, EventSubscription, WebhookEndpoint, DeliveryAttempt, enums
    bus.py               # EventBus — emit, listen, query
    subscriptions.py     # SubscriptionManager — webhook + subscription CRUD
    webhooks.py          # WebhookDelivery — outbound delivery with retry
```

## SQL Tables

- `events` — persisted event records
- `event_subscriptions` — subscription definitions with filters
- `webhook_endpoints` — registered outbound webhook targets
- `webhook_deliveries` — delivery attempt records with status tracking

## Invariants

1. **Events are immutable.** Once emitted, an event cannot be modified or deleted.
2. **Subscriptions are owner-governed.** Only the owner can delete a subscription.
3. **Webhook delivery is retryable.** Failed deliveries can be retried up to `max_retries`.
4. **Scope visibility applies.** Events are filtered by the caller's scope access.
