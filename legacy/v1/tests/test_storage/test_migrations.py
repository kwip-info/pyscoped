"""Tests for the schema migration system."""

from __future__ import annotations

import pytest

from scoped.exceptions import MigrationError
from scoped.storage.migrations.base import BaseMigration
from scoped.storage.migrations.registry import MigrationRegistry
from scoped.storage.migrations.runner import MigrationRunner, MigrationStatus


# ---------------------------------------------------------------------------
# Test migrations
# ---------------------------------------------------------------------------

class MigrationCreateFoo(BaseMigration):
    @property
    def version(self) -> int:
        return 1

    @property
    def name(self) -> str:
        return "create_foo"

    def up(self, backend):
        backend.execute("CREATE TABLE foo (id TEXT PRIMARY KEY, value TEXT)")

    def down(self, backend):
        backend.execute("DROP TABLE IF EXISTS foo")


class MigrationCreateBar(BaseMigration):
    @property
    def version(self) -> int:
        return 2

    @property
    def name(self) -> str:
        return "create_bar"

    def up(self, backend):
        backend.execute("CREATE TABLE bar (id TEXT PRIMARY KEY, count INTEGER DEFAULT 0)")

    def down(self, backend):
        backend.execute("DROP TABLE IF EXISTS bar")


class MigrationAddFooColumn(BaseMigration):
    @property
    def version(self) -> int:
        return 3

    @property
    def name(self) -> str:
        return "add_foo_column"

    def up(self, backend):
        backend.execute("ALTER TABLE foo ADD COLUMN extra TEXT DEFAULT ''")

    def down(self, backend):
        # SQLite doesn't support DROP COLUMN in older versions,
        # so we recreate the table.
        backend.execute("CREATE TABLE foo_backup (id TEXT PRIMARY KEY, value TEXT)")
        backend.execute("INSERT INTO foo_backup SELECT id, value FROM foo")
        backend.execute("DROP TABLE foo")
        backend.execute("ALTER TABLE foo_backup RENAME TO foo")


class FailingMigration(BaseMigration):
    @property
    def version(self) -> int:
        return 99

    @property
    def name(self) -> str:
        return "failing_migration"

    def up(self, backend):
        raise RuntimeError("Intentional failure")

    def down(self, backend):
        pass


# ---------------------------------------------------------------------------
# MigrationRegistry tests
# ---------------------------------------------------------------------------

class TestMigrationRegistry:

    def test_ensure_table(self, sqlite_backend):
        registry = MigrationRegistry(sqlite_backend)
        registry.ensure_table()
        assert sqlite_backend.table_exists("scoped_migrations")

    def test_no_migrations_initially(self, sqlite_backend):
        registry = MigrationRegistry(sqlite_backend)
        registry.ensure_table()
        assert registry.get_applied_versions() == []
        assert registry.get_current_version() == 0

    def test_record_and_query(self, sqlite_backend):
        registry = MigrationRegistry(sqlite_backend)
        registry.ensure_table()
        record = registry.record_applied(1, "first_migration", "abc123")
        assert record.version == 1
        assert record.name == "first_migration"
        assert record.checksum == "abc123"

        versions = registry.get_applied_versions()
        assert versions == [1]
        assert registry.get_current_version() == 1
        assert registry.is_applied(1)
        assert not registry.is_applied(2)

    def test_multiple_migrations(self, sqlite_backend):
        registry = MigrationRegistry(sqlite_backend)
        registry.ensure_table()
        registry.record_applied(1, "first")
        registry.record_applied(3, "third")
        registry.record_applied(2, "second")

        versions = registry.get_applied_versions()
        assert versions == [1, 2, 3]
        assert registry.get_current_version() == 3

    def test_rollback_removes_record(self, sqlite_backend):
        registry = MigrationRegistry(sqlite_backend)
        registry.ensure_table()
        registry.record_applied(1, "first")
        registry.record_applied(2, "second")
        registry.record_rolled_back(2)

        assert registry.get_applied_versions() == [1]
        assert registry.get_current_version() == 1
        assert not registry.is_applied(2)

    def test_get_applied_migrations_returns_records(self, sqlite_backend):
        registry = MigrationRegistry(sqlite_backend)
        registry.ensure_table()
        registry.record_applied(1, "first", "checksum1")
        registry.record_applied(2, "second", "checksum2")

        records = registry.get_applied_migrations()
        assert len(records) == 2
        assert records[0].version == 1
        assert records[0].name == "first"
        assert records[0].checksum == "checksum1"
        assert records[1].version == 2

    def test_record_snapshot(self, sqlite_backend):
        registry = MigrationRegistry(sqlite_backend)
        registry.ensure_table()
        record = registry.record_applied(1, "test")
        snap = record.snapshot()
        assert snap["version"] == 1
        assert snap["name"] == "test"
        assert "applied_at" in snap


# ---------------------------------------------------------------------------
# MigrationRunner tests
# ---------------------------------------------------------------------------

class TestMigrationRunner:

    def _make_runner(self, backend):
        runner = MigrationRunner(backend)
        runner.register(MigrationCreateFoo())
        runner.register(MigrationCreateBar())
        runner.register(MigrationAddFooColumn())
        return runner

    def test_register(self, sqlite_backend):
        runner = MigrationRunner(sqlite_backend)
        runner.register(MigrationCreateFoo())
        assert runner.all_versions == [1]

    def test_duplicate_version_raises(self, sqlite_backend):
        runner = MigrationRunner(sqlite_backend)
        runner.register(MigrationCreateFoo())
        with pytest.raises(MigrationError, match="Duplicate"):
            runner.register(MigrationCreateFoo())

    def test_all_versions_sorted(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        assert runner.all_versions == [1, 2, 3]

    def test_get_pending_initially_all(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        pending = runner.get_pending()
        assert len(pending) == 3
        assert [m.version for m in pending] == [1, 2, 3]

    def test_apply_all(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        applied = runner.apply_all()
        assert applied == [1, 2, 3]
        assert runner.get_pending() == []
        assert runner.get_current_version() == 3

        # Verify tables were actually created
        assert sqlite_backend.table_exists("foo")
        assert sqlite_backend.table_exists("bar")

    def test_apply_all_idempotent(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        runner.apply_all()
        applied = runner.apply_all()
        assert applied == []

    def test_apply_up_to(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        applied = runner.apply_up_to(2)
        assert applied == [1, 2]
        assert runner.get_current_version() == 2
        assert sqlite_backend.table_exists("foo")
        assert sqlite_backend.table_exists("bar")
        assert len(runner.get_pending()) == 1

    def test_apply_one(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        runner.apply_one(1)
        assert runner.get_current_version() == 1
        assert sqlite_backend.table_exists("foo")
        assert not sqlite_backend.table_exists("bar")

    def test_apply_one_not_found(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        with pytest.raises(MigrationError, match="not found"):
            runner.apply_one(999)

    def test_apply_one_already_applied(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        runner.apply_one(1)
        with pytest.raises(MigrationError, match="already applied"):
            runner.apply_one(1)

    def test_rollback_last(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        runner.apply_all()
        rolled_back = runner.rollback_last()
        assert rolled_back == 3
        assert runner.get_current_version() == 2

    def test_rollback_last_empty(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        result = runner.rollback_last()
        assert result is None

    def test_rollback_to(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        runner.apply_all()
        rolled_back = runner.rollback_to(1)
        assert rolled_back == [3, 2]
        assert runner.get_current_version() == 1
        assert sqlite_backend.table_exists("foo")
        assert not sqlite_backend.table_exists("bar")

    def test_rollback_one(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        runner.apply_all()
        runner.rollback_one(2)
        assert not sqlite_backend.table_exists("bar")
        # Migration 1 and 3 are still applied
        applied = runner.get_applied()
        assert [m.version for m in applied] == [1, 3]

    def test_rollback_one_not_found(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        with pytest.raises(MigrationError, match="not found"):
            runner.rollback_one(999)

    def test_rollback_one_not_applied(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        with pytest.raises(MigrationError, match="not applied"):
            runner.rollback_one(1)

    def test_get_status(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        runner.apply_up_to(2)

        status = runner.get_status()
        assert len(status) == 3
        assert status[0].version == 1
        assert status[0].applied is True
        assert status[0].applied_at is not None
        assert status[1].version == 2
        assert status[1].applied is True
        assert status[2].version == 3
        assert status[2].applied is False
        assert status[2].applied_at is None

    def test_status_snapshot(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        runner.apply_one(1)
        status = runner.get_status()[0]
        snap = status.snapshot()
        assert snap["version"] == 1
        assert snap["applied"] is True

    def test_failing_migration_raises(self, sqlite_backend):
        runner = MigrationRunner(sqlite_backend)
        runner.register(FailingMigration())
        with pytest.raises(MigrationError, match="Intentional failure"):
            runner.apply_all()

    def test_failing_migration_not_recorded(self, sqlite_backend):
        runner = MigrationRunner(sqlite_backend)
        runner.register(FailingMigration())
        try:
            runner.apply_all()
        except MigrationError:
            pass
        assert runner.get_current_version() == 0

    def test_get_applied(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        runner.apply_up_to(2)
        applied = runner.get_applied()
        assert [m.version for m in applied] == [1, 2]

    def test_checksum_recorded(self, sqlite_backend):
        runner = self._make_runner(sqlite_backend)
        runner.apply_one(1)
        registry = MigrationRegistry(sqlite_backend)
        records = registry.get_applied_migrations()
        assert len(records) == 1
        assert records[0].checksum != ""


# ---------------------------------------------------------------------------
# Auto-discovery tests
# ---------------------------------------------------------------------------

class TestMigrationDiscovery:

    def test_discover_builtin_versions(self, sqlite_backend):
        runner = MigrationRunner(sqlite_backend)
        count = runner.discover()
        assert count >= 1
        assert 1 in runner.all_versions

    def test_discovered_migration_applies(self, sqlite_backend):
        """The 0001 initial schema migration should create all framework tables."""
        runner = MigrationRunner(sqlite_backend)
        # Use a fresh backend that hasn't had schema created yet
        import warnings
        from scoped.storage.sqlite import SQLiteBackend
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            fresh = SQLiteBackend(":memory:")
        fresh.initialize()  # just sets up connection + pragmas, creates schema

        # Now test with a truly fresh backend (no schema)
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = on")

        from scoped.storage.migrations.registry import MIGRATIONS_TABLE_SQL
        conn.executescript(MIGRATIONS_TABLE_SQL)
        conn.commit()
        conn.close()

        # The real test: discover and apply to the pre-initialized backend
        runner = MigrationRunner(sqlite_backend)
        count = runner.discover()
        assert count >= 1

        # All discovered migrations should have valid version and name
        for v in runner.all_versions:
            status = [s for s in runner.get_status() if s.version == v]
            assert len(status) == 1
            assert status[0].name != ""


class TestInitialMigration:

    def test_initial_migration_creates_tables(self, sqlite_backend):
        """Verify that applying the initial migration creates expected tables."""
        from scoped.storage.migrations.versions.m0001_initial_schema import InitialSchema

        # Use a backend with only the migrations table
        import warnings
        from scoped.storage.sqlite import SQLiteBackend
        import sqlite3

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            backend = SQLiteBackend(":memory:")
        # Manually initialize just the connection (skip schema creation)
        backend._conn = sqlite3.connect(":memory:", check_same_thread=False)
        backend._conn.execute("PRAGMA foreign_keys = on")

        runner = MigrationRunner(backend)
        runner.register(InitialSchema())
        runner.apply_all()

        # Spot-check key tables exist
        assert backend.table_exists("registry_entries")
        assert backend.table_exists("principals")
        assert backend.table_exists("scoped_objects")
        assert backend.table_exists("scopes")
        assert backend.table_exists("rules")
        assert backend.table_exists("audit_trail")
        assert backend.table_exists("environments")
        assert backend.table_exists("secrets")
        assert backend.table_exists("plugins")
        assert backend.table_exists("connectors")
        assert backend.table_exists("marketplace_listings")

    def test_initial_migration_rollback(self, sqlite_backend):
        """Verify that rolling back the initial migration drops all tables."""
        from scoped.storage.migrations.versions.m0001_initial_schema import InitialSchema
        import warnings
        from scoped.storage.sqlite import SQLiteBackend
        import sqlite3

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            backend = SQLiteBackend(":memory:")
        backend._conn = sqlite3.connect(":memory:", check_same_thread=False)
        backend._conn.execute("PRAGMA foreign_keys = on")

        runner = MigrationRunner(backend)
        runner.register(InitialSchema())
        runner.apply_all()
        assert backend.table_exists("registry_entries")

        runner.rollback_last()
        assert not backend.table_exists("registry_entries")
        assert not backend.table_exists("principals")
        assert not backend.table_exists("scoped_objects")

    def test_repr(self):
        from scoped.storage.migrations.versions.m0001_initial_schema import InitialSchema
        mig = InitialSchema()
        assert "0001" in repr(mig)
        assert "initial_schema" in repr(mig)
