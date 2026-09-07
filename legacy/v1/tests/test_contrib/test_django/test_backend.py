"""Tests for DjangoORMBackend."""

from __future__ import annotations

import pytest

django = pytest.importorskip("django")

from scoped.contrib.django.backend import (
    DjangoORMBackend,
    _adapt_schema,
    _translate_placeholders,
)


class TestPlaceholderTranslation:
    def test_simple_replacement(self):
        assert _translate_placeholders("SELECT * FROM t WHERE id = ?") == (
            "SELECT * FROM t WHERE id = %s"
        )

    def test_multiple_placeholders(self):
        result = _translate_placeholders("INSERT INTO t (a, b) VALUES (?, ?)")
        assert result == "INSERT INTO t (a, b) VALUES (%s, %s)"

    def test_preserves_quoted_question_marks(self):
        result = _translate_placeholders("SELECT * FROM t WHERE name = '?'")
        assert result == "SELECT * FROM t WHERE name = '?'"

    def test_mixed_quoted_and_unquoted(self):
        result = _translate_placeholders("SELECT * FROM t WHERE a = ? AND b = '?'")
        assert result == "SELECT * FROM t WHERE a = %s AND b = '?'"

    def test_no_placeholders(self):
        sql = "SELECT 1"
        assert _translate_placeholders(sql) == sql


class TestSchemaAdaptation:
    def test_sqlite_passthrough(self):
        schema = "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT)"
        assert _adapt_schema(schema, "sqlite") == schema

    def test_postgresql_serial(self):
        schema = "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT)"
        result = _adapt_schema(schema, "postgresql")
        assert "SERIAL PRIMARY KEY" in result
        assert "AUTOINCREMENT" not in result


class TestDjangoORMBackend:
    def test_initialize_creates_tables(self):
        backend = DjangoORMBackend()
        backend.initialize()

        assert backend.table_exists("registry_entries")
        assert backend.table_exists("principals")
        assert backend.table_exists("scoped_objects")
        assert backend.table_exists("audit_trail")

    def test_execute_and_fetch(self):
        backend = DjangoORMBackend()
        backend.initialize()

        # Insert via execute
        backend.execute(
            "INSERT INTO registry_entries "
            "(id, urn, kind, namespace, name, registered_at, registered_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test-1", "scoped:MODEL:test:foo:1", "MODEL", "test", "foo", "2024-01-01", "system"),
        )

        # Fetch one
        row = backend.fetch_one("SELECT * FROM registry_entries WHERE id = ?", ("test-1",))
        assert row is not None
        assert row["urn"] == "scoped:MODEL:test:foo:1"

        # Fetch all
        rows = backend.fetch_all("SELECT * FROM registry_entries")
        assert len(rows) >= 1

    def test_fetch_one_returns_none(self):
        backend = DjangoORMBackend()
        backend.initialize()

        row = backend.fetch_one("SELECT * FROM registry_entries WHERE id = ?", ("nope",))
        assert row is None

    def test_fetch_all_returns_empty(self):
        backend = DjangoORMBackend()
        backend.initialize()

        rows = backend.fetch_all(
            "SELECT * FROM registry_entries WHERE id = ?", ("nope",)
        )
        assert rows == []

    def test_transaction_commit(self):
        backend = DjangoORMBackend()
        backend.initialize()

        tx = backend.transaction()
        tx.execute(
            "INSERT INTO registry_entries "
            "(id, urn, kind, namespace, name, registered_at, registered_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("tx-1", "scoped:MODEL:test:tx:1", "MODEL", "test", "tx", "2024-01-01", "system"),
        )
        tx.commit()

        row = backend.fetch_one("SELECT * FROM registry_entries WHERE id = ?", ("tx-1",))
        assert row is not None

    def test_transaction_rollback(self):
        backend = DjangoORMBackend()
        backend.initialize()

        tx = backend.transaction()
        tx.execute(
            "INSERT INTO registry_entries "
            "(id, urn, kind, namespace, name, registered_at, registered_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("tx-rb", "scoped:MODEL:test:rb:1", "MODEL", "test", "rb", "2024-01-01", "system"),
        )
        tx.rollback()

        row = backend.fetch_one("SELECT * FROM registry_entries WHERE id = ?", ("tx-rb",))
        assert row is None

    def test_transaction_fetch_one(self):
        backend = DjangoORMBackend()
        backend.initialize()

        backend.execute(
            "INSERT INTO registry_entries "
            "(id, urn, kind, namespace, name, registered_at, registered_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("tf-1", "scoped:MODEL:test:tf:1", "MODEL", "test", "tf", "2024-01-01", "system"),
        )

        tx = backend.transaction()
        row = tx.fetch_one("SELECT * FROM registry_entries WHERE id = ?", ("tf-1",))
        tx.commit()

        assert row is not None
        assert row["id"] == "tf-1"

    def test_transaction_fetch_all(self):
        backend = DjangoORMBackend()
        backend.initialize()

        tx = backend.transaction()
        rows = tx.fetch_all("SELECT * FROM registry_entries WHERE id = ?", ("nope",))
        tx.commit()

        assert rows == []

    def test_table_exists(self):
        backend = DjangoORMBackend()
        backend.initialize()

        assert backend.table_exists("registry_entries")
        assert not backend.table_exists("nonexistent_table")

    def test_works_with_scoped_manager(self):
        """Verify the Django backend works with core Scoped operations."""
        backend = DjangoORMBackend()
        backend.initialize()

        from scoped.identity.principal import PrincipalStore
        from scoped.objects.manager import ScopedManager

        principals = PrincipalStore(backend)
        user = principals.create_principal(kind="user", display_name="Django User")

        manager = ScopedManager(backend)
        obj, ver = manager.create(
            object_type="document",
            owner_id=user.id,
            data={"title": "Test"},
        )

        fetched = manager.get(obj.id, principal_id=user.id)
        assert fetched is not None
        assert fetched.id == obj.id
