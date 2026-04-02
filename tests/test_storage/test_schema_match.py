"""Validate that _schema.py Table definitions match the actual database schema.

This test initializes a fresh SQLite backend (which runs all migrations),
then compares the actual table/column structure against the SQLAlchemy
MetaData defined in ``scoped.storage._schema``.
"""

from __future__ import annotations

import sqlalchemy as sa
import pytest

from scoped.storage._schema import metadata as sa_metadata
from scoped.storage.sqlite import SQLiteBackend


@pytest.fixture
def live_backend():
    """Fresh in-memory SQLite with all migrations applied."""
    backend = SQLiteBackend(":memory:")
    backend.initialize()
    return backend


@pytest.fixture
def live_engine(live_backend):
    """Wrap the live SQLite connection as a SQLAlchemy engine for introspection."""
    return sa.create_engine("sqlite://", creator=lambda: live_backend.connection)


class TestSchemaMatch:
    def test_all_sa_tables_exist_in_db(self, live_backend):
        """Every table in _schema.py exists in the actual database."""
        # scoped_migrations is created by MigrationRunner, not initialize()
        skip = {"search_index_fts", "scoped_migrations"}
        for table_name in sa_metadata.tables:
            if table_name in skip:
                continue
            assert live_backend.table_exists(table_name), (
                f"Table {table_name!r} defined in _schema.py "
                f"but missing from actual database"
            )

    def test_all_db_tables_in_sa_metadata(self, live_backend):
        """Every table in the database is defined in _schema.py."""
        # Get actual tables from sqlite_master
        rows = live_backend.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' "
            "AND name NOT LIKE 'search_index_fts%' "
            "ORDER BY name",
            (),
        )
        db_tables = {r["name"] for r in rows}
        sa_tables = set(sa_metadata.tables.keys())

        missing = db_tables - sa_tables
        assert not missing, (
            f"Tables in database but not in _schema.py: {missing}"
        )

    def test_column_names_match(self, live_backend):
        """Every column in _schema.py tables exists in the actual database."""
        for table_name, sa_table in sa_metadata.tables.items():
            if not live_backend.table_exists(table_name):
                continue

            # Get actual columns from PRAGMA
            pragma_rows = live_backend.fetch_all(
                f"PRAGMA table_info({table_name})", ()
            )
            db_columns = {r["name"] for r in pragma_rows}
            sa_columns = {c.name for c in sa_table.columns}

            missing_in_db = sa_columns - db_columns
            assert not missing_in_db, (
                f"Table {table_name!r}: columns in _schema.py but not in DB: "
                f"{missing_in_db}"
            )

    def test_table_count(self, live_backend):
        """Sanity check: reasonable number of tables."""
        rows = live_backend.fetch_all(
            "SELECT COUNT(*) AS cnt FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'",
            (),
        )
        count = rows[0]["cnt"]
        # We expect 60+ tables (39 from m0001 + ~24 from m0002-m0011 + 1 scoped_migrations)
        assert count >= 55, f"Expected 55+ tables, got {count}"


class TestQueryCompilation:
    def test_compile_select_sqlite(self):
        from scoped.storage._query import compile_for
        from scoped.storage._schema import principals

        stmt = sa.select(principals).where(principals.c.id == "alice")
        sql, params = compile_for(stmt, dialect="sqlite")

        assert "principals" in sql
        assert "WHERE" in sql
        assert params == ("alice",)

    def test_compile_insert_sqlite(self):
        from scoped.storage._query import compile_for
        from scoped.storage._schema import principals

        stmt = sa.insert(principals).values(
            id="p1", kind="user", display_name="Alice",
            registry_entry_id="r1", created_at="2024-01-01",
        )
        sql, params = compile_for(stmt, dialect="sqlite")

        assert "INSERT" in sql
        assert "principals" in sql
        assert "p1" in params

    def test_compile_select_postgres(self):
        from scoped.storage._query import compile_for
        from scoped.storage._schema import scoped_objects

        stmt = sa.select(scoped_objects).where(
            scoped_objects.c.owner_id == "bob"
        )
        sql, params = compile_for(stmt, dialect="postgres")

        # Postgres uses %s placeholders (or %(name)s for named)
        assert "scoped_objects" in sql
        assert params == ("bob",)

    def test_compile_update(self):
        from scoped.storage._query import compile_for
        from scoped.storage._schema import scopes

        stmt = (
            sa.update(scopes)
            .where(scopes.c.id == "s1")
            .values(lifecycle="ARCHIVED")
        )
        sql, params = compile_for(stmt, dialect="sqlite")

        assert "UPDATE" in sql
        assert "ARCHIVED" in params
        assert "s1" in params

    def test_unsupported_dialect_raises(self):
        from scoped.storage._query import compile_for
        from scoped.storage._schema import principals

        stmt = sa.select(principals)
        with pytest.raises(ValueError, match="Unsupported dialect"):
            compile_for(stmt, dialect="mysql")
