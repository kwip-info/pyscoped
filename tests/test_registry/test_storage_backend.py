"""Tests for the SQLite storage backend itself."""

import pytest

from scoped.storage.sa_sqlite import SASQLiteBackend as SQLiteBackend


class TestSQLiteBackend:
    def test_initialize_creates_tables(self, sqlite_backend: SQLiteBackend):
        assert sqlite_backend.table_exists("registry_entries")
        assert sqlite_backend.table_exists("principals")
        assert sqlite_backend.table_exists("principal_relationships")
        assert sqlite_backend.table_exists("scoped_objects")
        assert sqlite_backend.table_exists("object_versions")
        assert sqlite_backend.table_exists("tombstones")
        assert sqlite_backend.table_exists("scopes")
        assert sqlite_backend.table_exists("scope_memberships")
        assert sqlite_backend.table_exists("scope_projections")
        assert sqlite_backend.table_exists("rules")
        assert sqlite_backend.table_exists("rule_versions")
        assert sqlite_backend.table_exists("rule_bindings")
        assert sqlite_backend.table_exists("audit_trail")
        # Environments
        assert sqlite_backend.table_exists("environments")
        assert sqlite_backend.table_exists("environment_templates")
        assert sqlite_backend.table_exists("environment_snapshots")
        assert sqlite_backend.table_exists("environment_objects")
        # Flow
        assert sqlite_backend.table_exists("pipelines")
        assert sqlite_backend.table_exists("stages")
        assert sqlite_backend.table_exists("stage_transitions")
        assert sqlite_backend.table_exists("flow_channels")
        assert sqlite_backend.table_exists("promotions")
        # Deployments
        assert sqlite_backend.table_exists("deployment_targets")
        assert sqlite_backend.table_exists("deployments")
        assert sqlite_backend.table_exists("deployment_gates")
        # Secrets
        assert sqlite_backend.table_exists("secrets")
        assert sqlite_backend.table_exists("secret_versions")
        assert sqlite_backend.table_exists("secret_refs")
        assert sqlite_backend.table_exists("secret_access_log")
        assert sqlite_backend.table_exists("secret_policies")
        # Integrations & Plugins
        assert sqlite_backend.table_exists("integrations")
        assert sqlite_backend.table_exists("plugins")
        assert sqlite_backend.table_exists("plugin_hooks")
        assert sqlite_backend.table_exists("plugin_permissions")
        # Connector & Marketplace
        assert sqlite_backend.table_exists("connectors")
        assert sqlite_backend.table_exists("connector_policies")
        assert sqlite_backend.table_exists("connector_traffic")
        assert sqlite_backend.table_exists("marketplace_listings")
        assert sqlite_backend.table_exists("marketplace_reviews")
        assert sqlite_backend.table_exists("marketplace_installs")

    def test_execute_and_fetch(self, sqlite_backend: SQLiteBackend):
        sqlite_backend.execute(
            "INSERT INTO registry_entries (id, urn, kind, namespace, name, lifecycle, registered_at, registered_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("test-id", "scoped:MODEL:app:Test:1", "MODEL", "app", "Test", "ACTIVE", "2024-01-01T00:00:00Z", "system"),
        )

        row = sqlite_backend.fetch_one("SELECT * FROM registry_entries WHERE id = ?", ("test-id",))
        assert row is not None
        assert row["urn"] == "scoped:MODEL:app:Test:1"
        assert row["kind"] == "MODEL"

    def test_fetch_all(self, sqlite_backend: SQLiteBackend):
        for i in range(3):
            sqlite_backend.execute(
                "INSERT INTO registry_entries (id, urn, kind, namespace, name, lifecycle, registered_at, registered_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (f"id-{i}", f"scoped:MODEL:app:M{i}:1", "MODEL", "app", f"M{i}", "ACTIVE", "2024-01-01T00:00:00Z", "system"),
            )

        rows = sqlite_backend.fetch_all("SELECT * FROM registry_entries")
        assert len(rows) == 3

    def test_fetch_one_returns_none(self, sqlite_backend: SQLiteBackend):
        row = sqlite_backend.fetch_one("SELECT * FROM registry_entries WHERE id = ?", ("nope",))
        assert row is None

    def test_transaction_commit(self, sqlite_backend: SQLiteBackend):
        tx = sqlite_backend.transaction()
        tx.execute(
            "INSERT INTO registry_entries (id, urn, kind, namespace, name, lifecycle, registered_at, registered_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("tx-1", "scoped:MODEL:app:TX1:1", "MODEL", "app", "TX1", "ACTIVE", "2024-01-01T00:00:00Z", "system"),
        )
        tx.commit()

        row = sqlite_backend.fetch_one("SELECT * FROM registry_entries WHERE id = ?", ("tx-1",))
        assert row is not None

    def test_transaction_rollback(self, sqlite_backend: SQLiteBackend):
        tx = sqlite_backend.transaction()
        tx.execute(
            "INSERT INTO registry_entries (id, urn, kind, namespace, name, lifecycle, registered_at, registered_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("tx-2", "scoped:MODEL:app:TX2:1", "MODEL", "app", "TX2", "ACTIVE", "2024-01-01T00:00:00Z", "system"),
        )
        tx.rollback()

        row = sqlite_backend.fetch_one("SELECT * FROM registry_entries WHERE id = ?", ("tx-2",))
        assert row is None

    def test_transaction_fetch(self, sqlite_backend: SQLiteBackend):
        tx = sqlite_backend.transaction()
        tx.execute(
            "INSERT INTO registry_entries (id, urn, kind, namespace, name, lifecycle, registered_at, registered_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("tx-3", "scoped:MODEL:app:TX3:1", "MODEL", "app", "TX3", "ACTIVE", "2024-01-01T00:00:00Z", "system"),
        )
        row = tx.fetch_one("SELECT * FROM registry_entries WHERE id = ?", ("tx-3",))
        assert row is not None
        assert row["id"] == "tx-3"
        tx.commit()

    def test_table_exists_false(self, sqlite_backend: SQLiteBackend):
        assert not sqlite_backend.table_exists("nonexistent_table")

    def test_close_and_reopen(self):
        backend = SQLiteBackend(":memory:")
        backend.initialize()
        assert backend.table_exists("registry_entries")
        backend.close()

        with pytest.raises(RuntimeError):
            backend.table_exists("registry_entries")

    def test_not_initialized_raises(self):
        backend = SQLiteBackend(":memory:")
        with pytest.raises(RuntimeError, match="not initialized"):
            backend.table_exists("anything")
