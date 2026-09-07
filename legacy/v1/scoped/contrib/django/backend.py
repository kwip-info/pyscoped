"""Django ORM storage backend for Scoped.

Uses ``django.db.connection`` for raw SQL — Scoped owns its own schema
via DDL, not Django models. This gives us Django's connection pooling
and transaction machinery for free.
"""

from __future__ import annotations

import re
from typing import Any

from scoped.storage._sql_utils import translate_placeholders as _translate_placeholders
from scoped.storage.interface import StorageBackend, StorageTransaction


def _adapt_schema(schema_sql: str, vendor: str) -> str:
    """Adapt SQLite DDL to the target database dialect."""
    if vendor == "sqlite":
        return schema_sql
    adapted = schema_sql
    if vendor == "postgresql":
        # SQLite AUTOINCREMENT → PostgreSQL SERIAL
        adapted = re.sub(
            r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
            "SERIAL PRIMARY KEY",
            adapted,
            flags=re.IGNORECASE,
        )
    return adapted


class DjangoTransaction(StorageTransaction):
    """Transaction wrapping a Django database cursor inside ``atomic()``."""

    def __init__(self, connection) -> None:
        self._conn = connection
        self._cursor = connection.cursor()
        self._savepoint = None

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> Any:
        sql = _translate_placeholders(sql)
        self._cursor.execute(sql, params)
        return getattr(self._cursor, "lastrowid", None)

    def execute_many(self, sql: str, params_seq: list[tuple[Any, ...]]) -> None:
        sql = _translate_placeholders(sql)
        self._cursor.executemany(sql, params_seq)

    def fetch_one(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> dict[str, Any] | None:
        sql = _translate_placeholders(sql)
        self._cursor.execute(sql, params)
        row = self._cursor.fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in self._cursor.description]
        return dict(zip(columns, row))

    def fetch_all(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> list[dict[str, Any]]:
        sql = _translate_placeholders(sql)
        self._cursor.execute(sql, params)
        rows = self._cursor.fetchall()
        if not rows:
            return []
        columns = [desc[0] for desc in self._cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    def commit(self) -> None:
        self._conn.cursor().execute("RELEASE SAVEPOINT scoped_tx")

    def rollback(self) -> None:
        try:
            self._conn.cursor().execute("ROLLBACK TO SAVEPOINT scoped_tx")
        except Exception:
            pass


class DjangoORMBackend(StorageBackend):
    """StorageBackend that uses Django's database connection.

    Args:
        using: Django database alias (default ``"default"``).
        auto_create_tables: Whether ``initialize()`` creates Scoped tables.
    """

    def __init__(
        self,
        *,
        using: str = "default",
        auto_create_tables: bool = True,
    ) -> None:
        self._using = using
        self._auto_create = auto_create_tables

    @property
    def dialect(self) -> str:
        vendor = self._connection.vendor
        if vendor == "postgresql":
            return "postgres"
        return vendor  # "sqlite", etc.

    @property
    def _connection(self):
        from django.db import connections

        return connections[self._using]

    def initialize(self) -> None:
        if not self._auto_create:
            return
        from scoped.storage.sqlite import SCHEMA_SQL

        vendor = self._connection.vendor
        schema = _adapt_schema(SCHEMA_SQL, vendor)
        with self._connection.cursor() as cursor:
            for statement in schema.split(";"):
                # Strip SQL comments and whitespace
                lines = statement.split("\n")
                clean_lines = [ln for ln in lines if not ln.strip().startswith("--")]
                stmt = "\n".join(clean_lines).strip()
                if not stmt:
                    continue
                try:
                    cursor.execute(_translate_placeholders(stmt))
                except Exception:
                    # Table may already exist — CREATE IF NOT EXISTS
                    pass

    def transaction(self) -> DjangoTransaction:
        tx = DjangoTransaction(self._connection)
        # Use savepoints for nested transaction support.
        # Requires an active transaction block — the ScopedContextMiddleware
        # wraps requests in django.db.transaction.atomic() automatically.
        self._connection.cursor().execute("SAVEPOINT scoped_tx")
        return tx

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> Any:
        sql = _translate_placeholders(sql)
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params)
            return getattr(cursor, "lastrowid", None)

    def fetch_one(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> dict[str, Any] | None:
        sql = _translate_placeholders(sql)
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            if row is None:
                return None
            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, row))

    def fetch_all(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> list[dict[str, Any]]:
        sql = _translate_placeholders(sql)
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            if not rows:
                return []
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in rows]

    def execute_script(self, sql: str) -> None:
        with self._connection.cursor() as cursor:
            for statement in sql.split(";"):
                stmt = statement.strip()
                if stmt and not stmt.startswith("--"):
                    cursor.execute(_translate_placeholders(stmt))

    def close(self) -> None:
        self._connection.close()

    def table_exists(self, table_name: str) -> bool:
        return table_name in self._connection.introspection.table_names()
