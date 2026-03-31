"""Migration 0008: Events & Webhooks tables.

Layer 14 — events, event_subscriptions, webhook_endpoints, webhook_deliveries.
"""

from __future__ import annotations

from scoped.storage.interface import StorageBackend


def up(backend: StorageBackend) -> None:
    backend.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id              TEXT PRIMARY KEY,
            event_type      TEXT NOT NULL,
            actor_id        TEXT NOT NULL,
            target_type     TEXT NOT NULL,
            target_id       TEXT NOT NULL,
            timestamp       TEXT NOT NULL,
            scope_id        TEXT,
            data_json       TEXT NOT NULL DEFAULT '{}',
            source_trace_id TEXT,
            lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE'
        )
    """)
    backend.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
    backend.execute("CREATE INDEX IF NOT EXISTS idx_events_actor ON events(actor_id)")
    backend.execute("CREATE INDEX IF NOT EXISTS idx_events_scope ON events(scope_id)")
    backend.execute("CREATE INDEX IF NOT EXISTS idx_events_target ON events(target_type, target_id)")
    backend.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)")

    backend.execute("""
        CREATE TABLE IF NOT EXISTS event_subscriptions (
            id                  TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            owner_id            TEXT NOT NULL,
            event_types_json    TEXT NOT NULL DEFAULT '[]',
            target_types_json   TEXT NOT NULL DEFAULT '[]',
            scope_id            TEXT,
            webhook_endpoint_id TEXT,
            created_at          TEXT NOT NULL,
            lifecycle           TEXT NOT NULL DEFAULT 'ACTIVE',
            FOREIGN KEY (owner_id) REFERENCES principals(id)
        )
    """)
    backend.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_owner ON event_subscriptions(owner_id)")
    backend.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_lifecycle ON event_subscriptions(lifecycle)")

    backend.execute("""
        CREATE TABLE IF NOT EXISTS webhook_endpoints (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            owner_id        TEXT NOT NULL,
            url             TEXT NOT NULL,
            config_json     TEXT NOT NULL DEFAULT '{}',
            scope_id        TEXT,
            created_at      TEXT NOT NULL,
            lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
            FOREIGN KEY (owner_id) REFERENCES principals(id)
        )
    """)
    backend.execute("CREATE INDEX IF NOT EXISTS idx_webhooks_owner ON webhook_endpoints(owner_id)")
    backend.execute("CREATE INDEX IF NOT EXISTS idx_webhooks_lifecycle ON webhook_endpoints(lifecycle)")

    backend.execute("""
        CREATE TABLE IF NOT EXISTS webhook_deliveries (
            id                  TEXT PRIMARY KEY,
            event_id            TEXT NOT NULL REFERENCES events(id),
            webhook_endpoint_id TEXT NOT NULL REFERENCES webhook_endpoints(id),
            subscription_id     TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'pending',
            attempted_at        TEXT NOT NULL,
            attempt_number      INTEGER NOT NULL DEFAULT 0,
            response_status     INTEGER,
            response_body       TEXT,
            error_message       TEXT
        )
    """)
    backend.execute("CREATE INDEX IF NOT EXISTS idx_deliveries_event ON webhook_deliveries(event_id)")
    backend.execute("CREATE INDEX IF NOT EXISTS idx_deliveries_status ON webhook_deliveries(status)")
    backend.execute("CREATE INDEX IF NOT EXISTS idx_deliveries_endpoint ON webhook_deliveries(webhook_endpoint_id)")


def down(backend: StorageBackend) -> None:
    backend.execute("DROP TABLE IF EXISTS webhook_deliveries")
    backend.execute("DROP TABLE IF EXISTS webhook_endpoints")
    backend.execute("DROP TABLE IF EXISTS event_subscriptions")
    backend.execute("DROP TABLE IF EXISTS events")
