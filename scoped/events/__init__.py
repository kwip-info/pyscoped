"""Layer 14: Events & Webhooks.

An asynchronous, scoped event bus — the reactive counterpart to audit.
Events are typed, scoped occurrences. Subscriptions filter events to
interested principals. Webhooks deliver events to external HTTP endpoints.
"""

from scoped.events.bus import EventBus
from scoped.events.crypto import decrypt_config, encrypt_config, generate_webhook_key
from scoped.events.models import (
    DeliveryAttempt,
    DeliveryStatus,
    Event,
    EventSubscription,
    EventType,
    WebhookEndpoint,
    event_from_row,
    subscription_from_row,
    webhook_from_row,
)
from scoped.events.subscriptions import SubscriptionManager
from scoped.events.webhooks import WebhookDelivery

__all__ = [
    "DeliveryAttempt",
    "DeliveryStatus",
    "Event",
    "EventBus",
    "EventSubscription",
    "EventType",
    "SubscriptionManager",
    "WebhookDelivery",
    "WebhookEndpoint",
    "decrypt_config",
    "encrypt_config",
    "event_from_row",
    "generate_webhook_key",
    "subscription_from_row",
    "webhook_from_row",
]
