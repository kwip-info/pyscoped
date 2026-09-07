"""SQLAlchemy-backed PostgreSQL storage backend.

Drop-in replacement for ``PostgresBackend`` that uses SQLAlchemy Core for
connection management while remaining fully compatible with the
``StorageBackend`` interface (raw SQL strings accepted).

Requires ``sqlalchemy[postgresql]`` or ``psycopg[binary]`` for the
psycopg3 dialect driver.

Usage::

    from scoped.storage.sa_postgres import SAPostgresBackend

    backend = SAPostgresBackend("postgresql://user:pass@localhost/mydb")
    backend.initialize()
"""

from __future__ import annotations

import re
from typing import Any

import sqlalchemy as sa

from scoped.storage._schema import metadata as sa_metadata
from scoped.storage.interface import StorageBackend, StorageTransaction
from scoped.storage.sa_sqlite import _rewrite_sql_params

# psycopg3 uses the "postgresql+psycopg" driver in SQLAlchemy 2.0+
_DRIVER = "postgresql+psycopg"


def _exec(conn: sa.engine.Connection, sql: str, params: tuple | dict) -> sa.CursorResult:
    """Execute with automatic ? → :name rewriting."""
    new_sql, named = _rewrite_sql_params(sql, params)
    return conn.execute(sa.text(new_sql), named)


class _SAPostgresTransaction(StorageTransaction):
    """Transaction backed by a SQLAlchemy connection."""

    def __init__(self, conn: sa.engine.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> Any:
        _exec(self._conn, sql, params)
        return None

    def execute_many(self, sql: str, params_seq: list[tuple[Any, ...]]) -> None:
        for params in params_seq:
            _exec(self._conn, sql, params)

    def fetch_one(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> dict[str, Any] | None:
        result = _exec(self._conn, sql, params)
        row = result.mappings().fetchone()
        return dict(row) if row is not None else None

    def fetch_all(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> list[dict[str, Any]]:
        result = _exec(self._conn, sql, params)
        return [dict(row) for row in result.mappings().fetchall()]

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        self._conn.close()


class SAPostgresBackend(StorageBackend):
    """SQLAlchemy Core-backed PostgreSQL storage backend.

    Accepts the same raw SQL strings as ``PostgresBackend`` for backward
    compatibility.  Schema creation uses ``metadata.create_all()`` instead
    of inline DDL strings.  Connection pooling is handled by SQLAlchemy's
    built-in ``QueuePool``.

    Args:
        dsn: PostgreSQL connection string
             (e.g. ``"postgresql://user:pass@localhost/mydb"``).
        pool_size: Number of connections kept in the pool.
        max_overflow: Extra connections beyond ``pool_size`` allowed.
        pool_timeout: Seconds to wait for a connection.
        enable_rls: Enable row-level security context injection.
    """

    def __init__(
        self,
        dsn: str,
        *,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: float = 30.0,
        enable_rls: bool = False,
    ) -> None:
        # Ensure the DSN uses the psycopg3 driver
        if dsn.startswith("postgresql://"):
            self._dsn = dsn.replace("postgresql://", f"{_DRIVER}://", 1)
        elif dsn.startswith("postgres://"):
            self._dsn = dsn.replace("postgres://", f"{_DRIVER}://", 1)
        else:
            self._dsn = dsn
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._pool_timeout = pool_timeout
        self._enable_rls = enable_rls
        self._engine: sa.engine.Engine | None = None

    @property
    def dialect(self) -> str:
        return "postgres"

    @property
    def engine(self) -> sa.engine.Engine:
        if self._engine is None:
            raise RuntimeError(
                "SAPostgresBackend not initialized — call initialize() first"
            )
        return self._engine

    def initialize(self) -> None:
        self._engine = sa.create_engine(
            self._dsn,
            pool_size=self._pool_size,
            max_overflow=self._max_overflow,
            pool_timeout=self._pool_timeout,
        )
        sa_metadata.create_all(self._engine)

    def _get_rls_principal_id(self) -> str:
        from scoped.identity.context import ScopedContext

        ctx = ScopedContext.current_or_none()
        return ctx.principal_id if ctx else ""

    def _set_rls_context(
        self, conn: sa.engine.Connection, *, local: bool = False
    ) -> None:
        if not self._enable_rls:
            return
        principal_id = self._get_rls_principal_id()
        cmd = "SET LOCAL" if local else "SET"
        conn.execute(
            sa.text(f"{cmd} app.current_principal_id = :pid"),
            {"pid": principal_id},
        )

    def _reset_rls_context(self, conn: sa.engine.Connection) -> None:
        if not self._enable_rls:
            return
        conn.execute(sa.text("RESET app.current_principal_id"))

    def transaction(self) -> _SAPostgresTransaction:
        conn = self.engine.connect()
        self._set_rls_context(conn, local=True)
        return _SAPostgresTransaction(conn)

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> Any:
        with self.engine.connect() as conn:
            self._set_rls_context(conn, local=False)
            try:
                _exec(conn, sql, params)
                conn.commit()
            finally:
                self._reset_rls_context(conn)
        return None

    def fetch_one(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            self._set_rls_context(conn, local=False)
            try:
                result = _exec(conn, sql, params)
                row = result.mappings().fetchone()
                return dict(row) if row is not None else None
            finally:
                self._reset_rls_context(conn)

    def fetch_all(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            self._set_rls_context(conn, local=False)
            try:
                result = _exec(conn, sql, params)
                return [dict(row) for row in result.mappings().fetchall()]
            finally:
                self._reset_rls_context(conn)

    def execute_script(self, sql: str) -> None:
        with self.engine.connect() as conn:
            for statement in sql.split(";"):
                stmt = statement.strip()
                if stmt and not stmt.startswith("--"):
                    conn.execute(sa.text(stmt))
            conn.commit()

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def table_exists(self, table_name: str) -> bool:
        return sa.inspect(self.engine).has_table(table_name)
