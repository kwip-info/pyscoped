"""Tests for the SQLAlchemy-backed storage backends.

Validates that SASQLiteBackend is a drop-in replacement for SQLiteBackend
by running core CRUD operations and transaction tests.
"""

from __future__ import annotations

import pytest

from scoped.storage.sa_sqlite import SASQLiteBackend


@pytest.fixture
def sa_backend():
    """In-memory SQLAlchemy SQLite backend."""
    backend = SASQLiteBackend(":memory:")
    backend.initialize()
    yield backend
    backend.close()


class TestSASQLiteBackendBasics:
    def test_dialect(self, sa_backend):
        assert sa_backend.dialect == "sqlite"

    def test_table_exists(self, sa_backend):
        assert sa_backend.table_exists("principals")
        assert sa_backend.table_exists("scoped_objects")
        assert sa_backend.table_exists("audit_trail")
        assert not sa_backend.table_exists("nonexistent_table")

    def test_execute_and_fetch(self, sa_backend):
        sa_backend.execute(
            "INSERT INTO registry_entries "
            "(id, urn, kind, namespace, name, registered_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("r1", "urn:CLASS:test:thing:1", "CLASS", "test", "thing", "2024-01-01"),
        )
        row = sa_backend.fetch_one(
            "SELECT * FROM registry_entries WHERE id = ?", ("r1",)
        )
        assert row is not None
        assert row["id"] == "r1"
        assert row["kind"] == "CLASS"

    def test_fetch_all(self, sa_backend):
        for i in range(3):
            sa_backend.execute(
                "INSERT INTO registry_entries "
                "(id, urn, kind, namespace, name, registered_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"r{i}", f"urn:CLASS:test:item{i}:1", "CLASS", "test", f"item{i}", "2024-01-01"),
            )
        rows = sa_backend.fetch_all(
            "SELECT * FROM registry_entries WHERE kind = ?", ("CLASS",)
        )
        assert len(rows) == 3

    def test_fetch_one_returns_none(self, sa_backend):
        row = sa_backend.fetch_one(
            "SELECT * FROM registry_entries WHERE id = ?", ("nonexistent",)
        )
        assert row is None

    def test_fetch_all_returns_empty(self, sa_backend):
        rows = sa_backend.fetch_all(
            "SELECT * FROM registry_entries WHERE kind = ?", ("NONEXISTENT",)
        )
        assert rows == []


class TestSASQLiteTransactions:
    def test_transaction_commit(self, sa_backend):
        with sa_backend.transaction() as txn:
            txn.execute(
                "INSERT INTO registry_entries "
                "(id, urn, kind, namespace, name, registered_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("r1", "urn:CLASS:test:txn:1", "CLASS", "test", "txn", "2024-01-01"),
            )
            txn.commit()

        row = sa_backend.fetch_one(
            "SELECT * FROM registry_entries WHERE id = ?", ("r1",)
        )
        assert row is not None

    def test_transaction_rollback(self, sa_backend):
        with sa_backend.transaction() as txn:
            txn.execute(
                "INSERT INTO registry_entries "
                "(id, urn, kind, namespace, name, registered_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("r1", "urn:CLASS:test:txn:1", "CLASS", "test", "txn", "2024-01-01"),
            )
            txn.rollback()

        row = sa_backend.fetch_one(
            "SELECT * FROM registry_entries WHERE id = ?", ("r1",)
        )
        assert row is None

    def test_transaction_fetch_within(self, sa_backend):
        with sa_backend.transaction() as txn:
            txn.execute(
                "INSERT INTO registry_entries "
                "(id, urn, kind, namespace, name, registered_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("r1", "urn:CLASS:test:txn:1", "CLASS", "test", "txn", "2024-01-01"),
            )
            row = txn.fetch_one(
                "SELECT * FROM registry_entries WHERE id = ?", ("r1",)
            )
            assert row is not None
            assert row["id"] == "r1"
            txn.commit()

    def test_transaction_auto_rollback_on_exception(self, sa_backend):
        try:
            with sa_backend.transaction() as txn:
                txn.execute(
                    "INSERT INTO registry_entries "
                    "(id, urn, kind, namespace, name, registered_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("r1", "urn:CLASS:test:txn:1", "CLASS", "test", "txn", "2024-01-01"),
                )
                raise ValueError("simulated error")
        except ValueError:
            pass

        row = sa_backend.fetch_one(
            "SELECT * FROM registry_entries WHERE id = ?", ("r1",)
        )
        assert row is None


class TestSASQLiteExecuteScript:
    def test_execute_script_creates_tables(self, sa_backend):
        sa_backend.execute_script(
            "CREATE TABLE IF NOT EXISTS test_extra (id TEXT PRIMARY KEY, value TEXT);"
            "INSERT INTO test_extra VALUES ('k1', 'v1');"
        )
        row = sa_backend.fetch_one("SELECT * FROM test_extra WHERE id = ?", ("k1",))
        assert row is not None
        assert row["value"] == "v1"


class TestSASQLiteWithPrincipalStore:
    """End-to-end: use PrincipalStore with the SA backend."""

    def test_create_and_get_principal(self, sa_backend):
        from scoped.identity.principal import PrincipalStore

        store = PrincipalStore(sa_backend)
        principal = store.create_principal(kind="user", display_name="Alice")

        assert principal.display_name == "Alice"

        fetched = store.get_principal(principal.id)
        assert fetched.id == principal.id
        assert fetched.display_name == "Alice"

    def test_create_and_list_principals(self, sa_backend):
        from scoped.identity.principal import PrincipalStore

        store = PrincipalStore(sa_backend)
        store.create_principal(kind="user", display_name="Alice")
        store.create_principal(kind="user", display_name="Bob")

        principals = store.list_principals()
        assert len(principals) == 2


class TestSASQLiteWithScopedManager:
    """End-to-end: use ScopedManager with the SA backend."""

    def test_create_and_get_object(self, sa_backend):
        from scoped.audit.writer import AuditWriter
        from scoped.identity.principal import PrincipalStore
        from scoped.objects.manager import ScopedManager

        pstore = PrincipalStore(sa_backend)
        alice = pstore.create_principal(kind="user", display_name="Alice")

        writer = AuditWriter(sa_backend)
        mgr = ScopedManager(sa_backend, audit_writer=writer)

        obj, ver = mgr.create(
            object_type="document",
            owner_id=alice.id,
            data={"title": "Test Doc", "body": "Hello"},
        )
        assert obj.object_type == "document"
        assert ver.data["title"] == "Test Doc"

        fetched = mgr.get(obj.id, principal_id=alice.id)
        assert fetched is not None
        assert fetched.id == obj.id

    def test_update_creates_new_version(self, sa_backend):
        from scoped.audit.writer import AuditWriter
        from scoped.identity.principal import PrincipalStore
        from scoped.objects.manager import ScopedManager

        pstore = PrincipalStore(sa_backend)
        alice = pstore.create_principal(kind="user", display_name="Alice")

        writer = AuditWriter(sa_backend)
        mgr = ScopedManager(sa_backend, audit_writer=writer)

        obj, v1 = mgr.create(
            object_type="document",
            owner_id=alice.id,
            data={"title": "Draft"},
        )
        obj2, v2 = mgr.update(
            obj.id,
            principal_id=alice.id,
            data={"title": "Final"},
        )
        assert v2.version == 2
        assert v2.data["title"] == "Final"

    def test_audit_trail_recorded(self, sa_backend):
        from scoped.audit.writer import AuditWriter
        from scoped.audit.query import AuditQuery
        from scoped.identity.principal import PrincipalStore
        from scoped.objects.manager import ScopedManager

        pstore = PrincipalStore(sa_backend)
        alice = pstore.create_principal(kind="user", display_name="Alice")

        writer = AuditWriter(sa_backend)
        mgr = ScopedManager(sa_backend, audit_writer=writer)
        query = AuditQuery(sa_backend)

        mgr.create(
            object_type="document",
            owner_id=alice.id,
            data={"title": "Test"},
        )

        entries = query.query(actor_id=alice.id)
        assert len(entries) >= 1
