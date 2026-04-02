"""Tests for the PostgreSQL storage backend.

These tests are skipped when ``PYSCOPED_TEST_PG_DSN`` is not set or
when ``psycopg`` is not installed.
"""

from __future__ import annotations

import os

import pytest

_PG_DSN = os.environ.get("PYSCOPED_TEST_PG_DSN")

pytestmark = pytest.mark.skipif(
    not _PG_DSN,
    reason="PYSCOPED_TEST_PG_DSN not set",
)


def _skip_if_no_psycopg():
    try:
        import psycopg  # noqa: F401
        import psycopg_pool  # noqa: F401
    except ImportError:
        pytest.skip("psycopg or psycopg_pool not installed")


@pytest.fixture
def pg_backend():
    """Fresh Postgres backend for each test, cleaned up after."""
    _skip_if_no_psycopg()
    from scoped.storage.sa_postgres import SAPostgresBackend

    backend = SAPostgresBackend(_PG_DSN, pool_size=3)
    backend.initialize()
    yield backend

    # Clean up all tables
    tables = backend.fetch_all(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public'",
        (),
    )
    if tables:
        names = ", ".join(r["table_name"] for r in tables)
        backend.execute(f"DROP TABLE IF EXISTS {names} CASCADE")
    backend.close()


class TestPostgresBackendBasics:
    """Core backend interface tests."""

    def test_dialect(self, pg_backend):
        assert pg_backend.dialect == "postgres"

    def test_connectivity(self, pg_backend):
        row = pg_backend.fetch_one("SELECT 1 AS ok", ())
        assert row is not None
        assert row["ok"] == 1

    def test_table_exists(self, pg_backend):
        assert pg_backend.table_exists("registry_entries") is True
        assert pg_backend.table_exists("nonexistent_table_xyz") is False

    def test_execute_returns_none(self, pg_backend):
        result = pg_backend.execute(
            "INSERT INTO registry_entries "
            "(id, urn, kind, namespace, name, registered_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("test-id", "scoped:test:ns:n:1", "object", "ns", "n", "2026-01-01T00:00:00"),
        )
        assert result is None

    def test_fetch_one_returns_dict(self, pg_backend):
        pg_backend.execute(
            "INSERT INTO registry_entries "
            "(id, urn, kind, namespace, name, registered_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("dict-test", "scoped:test:ns:d:1", "object", "ns", "d", "2026-01-01T00:00:00"),
        )
        row = pg_backend.fetch_one(
            "SELECT id, urn FROM registry_entries WHERE id = ?",
            ("dict-test",),
        )
        assert row is not None
        assert row["id"] == "dict-test"
        assert row["urn"] == "scoped:test:ns:d:1"

    def test_fetch_all_returns_list_of_dicts(self, pg_backend):
        for i in range(3):
            pg_backend.execute(
                "INSERT INTO registry_entries "
                "(id, urn, kind, namespace, name, registered_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"all-{i}", f"scoped:test:ns:a{i}:1", "object", "ns", f"a{i}", "2026-01-01"),
            )
        rows = pg_backend.fetch_all("SELECT id FROM registry_entries ORDER BY id", ())
        assert len(rows) == 3
        assert all(isinstance(r, dict) for r in rows)

    def test_fetch_one_returns_none_when_empty(self, pg_backend):
        row = pg_backend.fetch_one(
            "SELECT id FROM registry_entries WHERE id = ?", ("nope",)
        )
        assert row is None

    def test_fetch_all_returns_empty_list(self, pg_backend):
        rows = pg_backend.fetch_all(
            "SELECT id FROM registry_entries WHERE id = ?", ("nope",)
        )
        assert rows == []


class TestPostgresTransactions:
    """Transaction commit and rollback."""

    def test_commit(self, pg_backend):
        tx = pg_backend.transaction()
        tx.execute(
            "INSERT INTO registry_entries "
            "(id, urn, kind, namespace, name, registered_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("tx-c", "scoped:test:ns:tc:1", "object", "ns", "tc", "2026-01-01"),
        )
        tx.commit()

        row = pg_backend.fetch_one("SELECT id FROM registry_entries WHERE id = ?", ("tx-c",))
        assert row is not None

    def test_rollback(self, pg_backend):
        tx = pg_backend.transaction()
        tx.execute(
            "INSERT INTO registry_entries "
            "(id, urn, kind, namespace, name, registered_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("tx-r", "scoped:test:ns:tr:1", "object", "ns", "tr", "2026-01-01"),
        )
        tx.rollback()

        row = pg_backend.fetch_one("SELECT id FROM registry_entries WHERE id = ?", ("tx-r",))
        assert row is None

    def test_context_manager_rollback_on_error(self, pg_backend):
        try:
            with pg_backend.transaction() as tx:
                tx.execute(
                    "INSERT INTO registry_entries "
                    "(id, urn, kind, namespace, name, registered_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("tx-e", "scoped:test:ns:te:1", "object", "ns", "te", "2026-01-01"),
                )
                raise RuntimeError("simulated failure")
        except RuntimeError:
            pass

        row = pg_backend.fetch_one("SELECT id FROM registry_entries WHERE id = ?", ("tx-e",))
        assert row is None

    def test_transaction_fetch_one(self, pg_backend):
        tx = pg_backend.transaction()
        tx.execute(
            "INSERT INTO registry_entries "
            "(id, urn, kind, namespace, name, registered_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("tx-f", "scoped:test:ns:tf:1", "object", "ns", "tf", "2026-01-01"),
        )
        row = tx.fetch_one("SELECT id FROM registry_entries WHERE id = ?", ("tx-f",))
        assert row is not None
        assert row["id"] == "tx-f"
        tx.commit()

    def test_transaction_fetch_all(self, pg_backend):
        tx = pg_backend.transaction()
        for i in range(3):
            tx.execute(
                "INSERT INTO registry_entries "
                "(id, urn, kind, namespace, name, registered_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"txfa-{i}", f"scoped:test:ns:txfa{i}:1", "object", "ns", f"txfa{i}", "2026-01-01"),
            )
        rows = tx.fetch_all("SELECT id FROM registry_entries ORDER BY id", ())
        assert len(rows) == 3
        tx.commit()


class TestPlaceholderTranslation:
    """Verify that ? placeholders work transparently."""

    def test_quoted_question_marks_preserved(self, pg_backend):
        """A literal '?' inside a SQL string should not be translated."""
        pg_backend.execute(
            "INSERT INTO registry_entries "
            "(id, urn, kind, namespace, name, registered_at, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("q-test", "scoped:test:ns:q:1", "object", "ns", "q", "2026-01-01", '{"key": "?"}'),
        )
        row = pg_backend.fetch_one(
            "SELECT metadata_json FROM registry_entries WHERE id = ?", ("q-test",)
        )
        assert row["metadata_json"] == '{"key": "?"}'


class TestSchemaCompleteness:
    """Verify all expected tables are created."""

    def test_core_tables_exist(self, pg_backend):
        core_tables = [
            "registry_entries",
            "principals",
            "scoped_objects",
            "object_versions",
            "tombstones",
            "scopes",
            "scope_memberships",
            "scope_projections",
            "rules",
            "rule_versions",
            "rule_bindings",
            "audit_trail",
        ]
        for table in core_tables:
            assert pg_backend.table_exists(table), f"Missing table: {table}"

    def test_search_index_has_tsvector(self, pg_backend):
        """search_index should have a search_vector tsvector column."""
        row = pg_backend.fetch_one(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'search_index' AND column_name = 'search_vector'",
            (),
        )
        assert row is not None
        assert row["data_type"] == "tsvector"

    def test_glacial_archives_uses_bytea(self, pg_backend):
        """compressed_data column should be BYTEA, not BLOB."""
        row = pg_backend.fetch_one(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = 'glacial_archives' AND column_name = 'compressed_data'",
            (),
        )
        assert row is not None
        assert row["data_type"] == "bytea"

    def test_no_fts5_virtual_table(self, pg_backend):
        """search_index_fts should NOT exist on Postgres."""
        assert pg_backend.table_exists("search_index_fts") is False
