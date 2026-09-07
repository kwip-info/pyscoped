"""Layer 15: Notifications.

Principal-targeted messages generated from events or rules.
Notifications have delivery channels, read state, and per-principal
preferences.
"""

from scoped.notifications.models import (
    Notification,
    NotificationChannel,
    NotificationPreference,
    NotificationRule,
    NotificationStatus,
    notification_from_row,
    preference_from_row,
    rule_from_row,
)
from scoped.notifications.engine import NotificationEngine
from scoped.notifications.delivery import DeliveryManager
from scoped.notifications.preferences import PreferenceManager

__all__ = [
    "DeliveryManager",
    "Notification",
    "NotificationChannel",
    "NotificationEngine",
    "NotificationPreference",
    "NotificationRule",
    "NotificationStatus",
    "PreferenceManager",
    "notification_from_row",
    "preference_from_row",
    "rule_from_row",
]
