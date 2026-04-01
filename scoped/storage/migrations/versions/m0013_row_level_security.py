"""Migration 0013: Row-level security policies.

Adds Postgres RLS policies to all tables with ``owner_id`` columns.
Policies reference ``current_setting('app.current_principal_id', true)``
which is set per-connection by the PostgresBackend when ``enable_rls=True``.

Tables without owner_id (audit_trail, registry_entries, etc.) are not
RLS-protected — they are either append-only or system-managed.

This migration is a no-op on SQLite (RLS is Postgres-only).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scoped.storage.migrations.base import BaseMigration

if TYPE_CHECKING:
    from scoped.storage.interface import StorageBackend


# Tables with owner_id that get RLS policies
_OWNER_TABLES = [
    "scoped_objects",
    "scopes",
    "secrets",
    "secret_versions",
    "environments",
    "environment_templates",
    "stages",
    "pipelines",
    "deployment_targets",
    "contracts",
    "blobs",
    "search_index",
    "templates",
    "retention_policies",
    "glacial_archives",
    "event_subscriptions",
    "webhook_endpoints",
    "notification_rules",
    "recurring_schedules",
    "scheduled_actions",
    "jobs",
]

# Tables with actor_id instead of owner_id
_ACTOR_TABLES = [
    ("audit_trail", "actor_id"),
]

# Membership/projection tables — visible if principal matches
_MEMBERSHIP_TABLES = [
    ("scope_memberships", "principal_id"),
]


class AddRowLevelSecurity(BaseMigration):
    @property
    def version(self) -> int:
        return 13

    @property
    def name(self) -> str:
        return "row_level_security"

    def up(self, backend: StorageBackend) -> None:
        if backend.dialect != "postgres":
            return

        for table in _OWNER_TABLES:
            backend.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            # FORCE ensures policies apply even when connected as the table owner
            backend.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            backend.execute(
                f"CREATE POLICY scoped_owner_isolation ON {table} "
                f"USING (owner_id = current_setting('app.current_principal_id', true))"
            )

        for table, col in _MEMBERSHIP_TABLES:
            backend.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            backend.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            backend.execute(
                f"CREATE POLICY scoped_member_isolation ON {table} "
                f"USING ({col} = current_setting('app.current_principal_id', true))"
            )

        # Scope projections: visible if the principal is a member of the scope
        backend.execute("ALTER TABLE scope_projections ENABLE ROW LEVEL SECURITY")
        backend.execute("ALTER TABLE scope_projections FORCE ROW LEVEL SECURITY")
        backend.execute(
            "CREATE POLICY scoped_projection_isolation ON scope_projections "
            "USING (scope_id IN ("
            "  SELECT scope_id FROM scope_memberships "
            "  WHERE principal_id = current_setting('app.current_principal_id', true) "
            "  AND lifecycle = 'ACTIVE'"
            "))"
        )

        # Notifications: visible to the recipient
        backend.execute("ALTER TABLE notifications ENABLE ROW LEVEL SECURITY")
        backend.execute("ALTER TABLE notifications FORCE ROW LEVEL SECURITY")
        backend.execute(
            "CREATE POLICY scoped_notification_isolation ON notifications "
            "USING (recipient_id = current_setting('app.current_principal_id', true))"
        )

    def down(self, backend: StorageBackend) -> None:
        if backend.dialect != "postgres":
            return

        for table in _OWNER_TABLES:
            backend.execute(f"DROP POLICY IF EXISTS scoped_owner_isolation ON {table}")
            backend.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            backend.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

        for table, _ in _MEMBERSHIP_TABLES:
            backend.execute(f"DROP POLICY IF EXISTS scoped_member_isolation ON {table}")
            backend.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            backend.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

        backend.execute("DROP POLICY IF EXISTS scoped_projection_isolation ON scope_projections")
        backend.execute("ALTER TABLE scope_projections NO FORCE ROW LEVEL SECURITY")
        backend.execute("ALTER TABLE scope_projections DISABLE ROW LEVEL SECURITY")

        backend.execute("DROP POLICY IF EXISTS scoped_notification_isolation ON notifications")
        backend.execute("ALTER TABLE notifications NO FORCE ROW LEVEL SECURITY")
        backend.execute("ALTER TABLE notifications DISABLE ROW LEVEL SECURITY")
