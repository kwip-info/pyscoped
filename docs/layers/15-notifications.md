# Layer 15: Notifications

## Purpose

Events (Layer 14) are system-level — they describe what happened. Notifications are **principal-level** — they tell a specific person that something happened and why they should care.

"Object 47 was updated" is an event. "Bob, your deployment was approved" is a notification.

## Dependencies

- **Layer 14 (Events)** — notifications are generated from events
- **Layer 5 (Rules)** — notification rules define when to notify whom
- **Layer 2 (Identity)** — notifications target specific principals
- **Layer 12 (Integrations)** — delivery channels use integration infrastructure

## Core Concepts

### Notification

A message targeting a specific principal. Notifications have lifecycle state — they can be unread, read, or dismissed.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `recipient_id` | Who receives this notification |
| `title` | Short summary |
| `body` | Full message |
| `channel` | Delivery channel (`IN_APP`, `EMAIL`, `SMS`, `PUSH`, `WEBHOOK`) |
| `status` | `UNREAD`, `READ`, `DISMISSED` |
| `created_at` | When it was generated |
| `source_event_id` | The event that triggered it |
| `source_rule_id` | The rule that matched |
| `scope_id` | Optional scope context |
| `data` | Arbitrary metadata |
| `read_at` | When the recipient read it |
| `dismissed_at` | When the recipient dismissed it |

### NotificationRule

Declarative rules that convert events into notifications. "When event X matches pattern Y, notify principal Z via channel C."

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `name` | Human-readable label |
| `owner_id` | Who created this rule |
| `event_types` | Filter: which event types trigger this rule |
| `target_types` | Filter: which target types match |
| `scope_id` | Filter: only events in this scope |
| `recipient_ids` | Who to notify (list of principal IDs) |
| `channel` | Delivery channel |
| `title_template` | Template for notification title (supports `{event_type}`, `{target_type}`, etc.) |
| `body_template` | Template for notification body |

### NotificationPreference

Per-principal delivery preferences. Principals can enable or disable specific channels.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `principal_id` | Whose preference |
| `channel` | Which channel |
| `enabled` | On or off |

### Notification Channels

Five built-in channels, extensible through integrations:

| Channel | Purpose |
|---------|---------|
| `IN_APP` | Default. Stored and queryable within the framework |
| `EMAIL` | Delivered via email integration |
| `SMS` | Delivered via SMS integration |
| `PUSH` | Delivered via push notification integration |
| `WEBHOOK` | Delivered via webhook endpoint |

## Architecture

### NotificationEngine

The core processor. Handles rule management, event-to-notification conversion, and notification lifecycle.

```python
engine = NotificationEngine(backend)

# Create a notification rule
rule = engine.create_rule(
    name="Deployment Alerts",
    owner_id=admin.id,
    event_types=["deployment_completed", "deployment_rolled_back"],
    recipient_ids=[ops_team.id, lead.id],
    channel=NotificationChannel.IN_APP,
    title_template="Deployment {event_type}",
    body_template="{target_type} {target_id} was {event_type}",
)

# Process an event — generates notifications for matching rules
notifications = engine.process_event(event)

# Query notifications for a principal
unread = engine.list_notifications(recipient_id=user.id, status=NotificationStatus.UNREAD)
count = engine.count_unread(user.id)

# Mark as read/dismissed
engine.mark_read(notification.id)
engine.mark_dismissed(notification.id)
```

### DeliveryManager

Tracks delivery state for notifications that need external delivery (email, SMS, push, webhook).

```python
delivery = DeliveryManager(backend)

# Get pending deliveries for a channel
pending = delivery.get_pending(channel=NotificationChannel.EMAIL)

# Mark as delivered after external delivery succeeds
delivery.mark_delivered(notification.id)

# Count pending
count = delivery.count_pending(channel=NotificationChannel.EMAIL)
```

### PreferenceManager

Per-principal channel preferences. If a principal disables a channel, the engine skips it.

```python
prefs = PreferenceManager(backend)

# Set preference
prefs.set_preference(
    principal_id=user.id,
    channel=NotificationChannel.EMAIL,
    enabled=False,
)

# Check before delivering
if prefs.is_channel_enabled(user.id, NotificationChannel.EMAIL):
    # deliver via email
    ...

# Get all preferences for a principal
all_prefs = prefs.get_preferences(user.id)
```

## Key Files

```
scoped/notifications/
    __init__.py          # Package exports
    models.py            # Notification, NotificationRule, NotificationPreference, enums
    engine.py            # NotificationEngine — rules, event processing, lifecycle
    delivery.py          # DeliveryManager — delivery tracking
    preferences.py       # PreferenceManager — per-principal settings
```

## SQL Tables

- `notifications` — notification records with lifecycle state
- `notification_rules` — event-to-notification mapping rules
- `notification_preferences` — per-principal channel settings
- `notification_deliveries` — external delivery tracking

## Invariants

1. **Notifications target principals.** Every notification has a `recipient_id`.
2. **Rules are declarative.** Notification generation is rule-driven, not ad-hoc.
3. **Preferences are respected.** Disabled channels are skipped.
4. **Lifecycle is tracked.** Unread → read → dismissed, with timestamps.
