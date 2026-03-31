"""Migration 0009: Notifications tables.

Layer 15 — notifications, notification_rules, notification_preferences.
"""

from __future__ import annotations

from scoped.storage.interface import StorageBackend


def up(backend: StorageBackend) -> None:
    backend.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id              TEXT PRIMARY KEY,
            recipient_id    TEXT NOT NULL,
            title           TEXT NOT NULL,
            body            TEXT NOT NULL DEFAULT '',
            channel         TEXT NOT NULL DEFAULT 'in_app',
            status          TEXT NOT NULL DEFAULT 'unread',
            created_at      TEXT NOT NULL,
            source_event_id TEXT,
            source_rule_id  TEXT,
            scope_id        TEXT,
            data_json       TEXT NOT NULL DEFAULT '{}',
            read_at         TEXT,
            dismissed_at    TEXT,
            lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE'
        )
    """)
    backend.execute("CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications(recipient_id)")
    backend.execute("CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status)")
    backend.execute("CREATE INDEX IF NOT EXISTS idx_notifications_channel ON notifications(channel)")
    backend.execute("CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at)")

    backend.execute("""
        CREATE TABLE IF NOT EXISTS notification_rules (
            id                  TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            owner_id            TEXT NOT NULL,
            event_types_json    TEXT NOT NULL DEFAULT '[]',
            target_types_json   TEXT NOT NULL DEFAULT '[]',
            scope_id            TEXT,
            recipient_ids_json  TEXT NOT NULL DEFAULT '[]',
            channel             TEXT NOT NULL DEFAULT 'in_app',
            title_template      TEXT NOT NULL DEFAULT '{event_type}',
            body_template       TEXT NOT NULL DEFAULT '{target_type} {target_id}',
            created_at          TEXT NOT NULL,
            lifecycle           TEXT NOT NULL DEFAULT 'ACTIVE',
            FOREIGN KEY (owner_id) REFERENCES principals(id)
        )
    """)
    backend.execute("CREATE INDEX IF NOT EXISTS idx_notif_rules_owner ON notification_rules(owner_id)")
    backend.execute("CREATE INDEX IF NOT EXISTS idx_notif_rules_lifecycle ON notification_rules(lifecycle)")

    backend.execute("""
        CREATE TABLE IF NOT EXISTS notification_preferences (
            id              TEXT PRIMARY KEY,
            principal_id    TEXT NOT NULL,
            channel         TEXT NOT NULL,
            enabled         INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT NOT NULL,
            lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
            UNIQUE(principal_id, channel)
        )
    """)
    backend.execute("CREATE INDEX IF NOT EXISTS idx_notif_prefs_principal ON notification_preferences(principal_id)")


def down(backend: StorageBackend) -> None:
    backend.execute("DROP TABLE IF EXISTS notification_preferences")
    backend.execute("DROP TABLE IF EXISTS notification_rules")
    backend.execute("DROP TABLE IF EXISTS notifications")
