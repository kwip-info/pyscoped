"""SQLAlchemy-backed SQLite storage backend.

Drop-in replacement for ``SQLiteBackend`` that uses SQLAlchemy Core for
connection management and schema creation while remaining fully compatible
with the ``StorageBackend`` interface (raw SQL strings accepted).

Usage::

    from scoped.storage.sa_sqlite import SASQLiteBackend

    backend = SASQLiteBackend(":memory:")
    backend.initialize()
    # Use exactly like SQLiteBackend
"""

from __future__ import annotations

import re
from typing import Any

import sqlalchemy as sa

from scoped.storage._schema import metadata as sa_metadata
from scoped.storage.interface import StorageBackend, StorageTransaction

# Regex: split by single-quoted strings to avoid replacing ? inside literals
_QUOTE_SPLIT = re.compile(r"('(?:[^']|'')*')")


def _rewrite_sql_params(
    sql: str, params: tuple[Any, ...] | dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Rewrite ``?``-style SQL + positional params to ``:name``-style + dict.

    SQLAlchemy's ``text()`` requires named parameters.  The existing
    pyscoped codebase uses ``?`` positional placeholders.  This function
    bridges the two styles.

    If ``params`` is already a dict, returns ``(sql, params)`` unchanged.
    """
    if isinstance(params, dict):
        return sql, params
    if not params:
        return sql, {}

    named: dict[str, Any] = {}
    idx = 0

    def _sub(_m: re.Match) -> str:
        nonlocal idx
        key = f"p{idx}"
        named[key] = params[idx]
        idx += 1
        return f":{key}"

    segments = _QUOTE_SPLIT.split(sql)
    new_segments = []
    for i, seg in enumerate(segments):
        if i % 2 == 0:  # outside quotes
            new_segments.append(re.sub(r"\?", _sub, seg))
        else:
            new_segments.append(seg)

    return "".join(new_segments), named


def _exec(conn: sa.engine.Connection, sql: str, params: tuple | dict) -> sa.CursorResult:
    """Execute with automatic ? → :name rewriting."""
    new_sql, named = _rewrite_sql_params(sql, params)
    return conn.execute(sa.text(new_sql), named)


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------


class _SASQLiteTransaction(StorageTransaction):
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
        # Close the connection and return it to the pool
        self._conn.close()


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class SASQLiteBackend(StorageBackend):
    """SQLAlchemy Core-backed SQLite storage backend.

    Accepts the same raw SQL strings as ``SQLiteBackend`` for backward
    compatibility.  Schema creation uses ``metadata.create_all()`` instead
    of inline DDL strings.

    Args:
        path: Database file path, or ``":memory:"`` for in-memory.
    """

    def __init__(self, path: str = ":memory:") -> None:
        self._path = path
        self._engine: sa.engine.Engine | None = None

    @property
    def dialect(self) -> str:
        return "sqlite"

    @property
    def engine(self) -> sa.engine.Engine:
        if self._engine is None:
            raise RuntimeError(
                "SASQLiteBackend not initialized — call initialize() first"
            )
        return self._engine

    def initialize(self) -> None:
        if self._path == ":memory:":
            self._engine = sa.create_engine(
                "sqlite://",
                connect_args={"check_same_thread": False},
                poolclass=sa.pool.StaticPool,
            )
        else:
            self._engine = sa.create_engine(
                f"sqlite:///{self._path}",
                connect_args={"check_same_thread": False},
                pool_size=1,
                max_overflow=0,
            )

        @sa.event.listens_for(self._engine, "connect")
        def _set_pragmas(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode = wal")
            cursor.execute("PRAGMA foreign_keys = on")
            cursor.execute("PRAGMA busy_timeout = 5000")
            cursor.close()

        sa_metadata.create_all(self._engine)

        # FTS5 virtual tables and composite indexes can't be represented
        # as SA Table objects.  Create them via raw DDL after the schema.
        _POST_CREATE_DDL = [
            # FTS5 (m0005)
            "CREATE VIRTUAL TABLE IF NOT EXISTS search_index_fts "
            "USING fts5(content, content_rowid='rowid')",
            # Composite indexes (m0012)
            "CREATE INDEX IF NOT EXISTS idx_projections_scope_lifecycle "
            "ON scope_projections(scope_id, lifecycle)",
            "CREATE INDEX IF NOT EXISTS idx_memberships_scope_lifecycle "
            "ON scope_memberships(scope_id, lifecycle)",
            "CREATE INDEX IF NOT EXISTS idx_memberships_principal_lifecycle "
            "ON scope_memberships(principal_id, lifecycle)",
            "CREATE INDEX IF NOT EXISTS idx_audit_action_timestamp "
            "ON audit_trail(action, timestamp)",
            # Audit sequence uniqueness (m0014)
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_audit_sequence "
            "ON audit_trail(sequence)",
        ]
        with self._engine.connect() as conn:
            for ddl in _POST_CREATE_DDL:
                conn.execute(sa.text(ddl))
            conn.commit()

    def transaction(self) -> _SASQLiteTransaction:
        conn = self.engine.connect()
        return _SASQLiteTransaction(conn)

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> Any:
        with self.engine.connect() as conn:
            _exec(conn, sql, params)
            conn.commit()
        return None

    def fetch_one(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            result = _exec(conn, sql, params)
            row = result.mappings().fetchone()
            return dict(row) if row is not None else None

    def fetch_all(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            result = _exec(conn, sql, params)
            return [dict(row) for row in result.mappings().fetchall()]

    def execute_script(self, sql: str) -> None:
        """Execute multiple SQL statements (DDL, migrations)."""
        with self.engine.connect() as conn:
            raw_conn = conn.connection.dbapi_connection
            raw_conn.executescript(sql)
            conn.commit()

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def table_exists(self, table_name: str) -> bool:
        return sa.inspect(self.engine).has_table(table_name)
