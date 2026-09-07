"""Query compilation bridge — SQLAlchemy Core → raw SQL + params.

This module allows incremental migration from raw SQL to SQLAlchemy Core
query constructs.  Instead of rewriting the entire ``StorageBackend``
interface, layer code can build queries using SQLAlchemy's ``select()``,
``insert()``, ``update()``, ``delete()`` DSL and then compile them to
the ``(sql_string, params_tuple)`` format that ``backend.execute()`` and
``backend.fetch_*()`` already accept.

Usage::

    from scoped.storage._schema import principals
    from scoped.storage._query import compile_for

    stmt = sa.select(principals).where(principals.c.id == "alice")
    sql, params = compile_for(stmt, dialect="sqlite")
    row = backend.fetch_one(sql, params)

The ``dialect`` parameter should match ``backend.dialect`` (``"sqlite"``
or ``"postgres"``).
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

_DIALECTS = {
    "sqlite": sqlite.dialect(),
    "postgres": postgresql.dialect(),
    "postgresql": postgresql.dialect(),
    "generic": sqlite.dialect(),  # safe fallback for DjangoORMBackend etc.
}


def dialect_insert(
    table: sa.Table,
    dialect: str = "sqlite",
) -> sa.sql.dml.Insert:
    """Return a dialect-aware INSERT that supports ``.on_conflict_do_update()``.

    Standard ``sa.insert()`` does not expose ``on_conflict_do_update``.
    Use this helper when you need UPSERT behaviour.

    >>> stmt = dialect_insert(principals, "sqlite").values(id="p1", ...)
    >>> stmt = stmt.on_conflict_do_update(
    ...     index_elements=["id"],
    ...     set_={"display_name": stmt.excluded.display_name},
    ... )
    """
    if dialect in ("postgres", "postgresql"):
        return pg_insert(table)
    return sqlite_insert(table)


def compile_for(
    stmt: sa.sql.ClauseElement,
    dialect: str = "sqlite",
) -> tuple[str, tuple[Any, ...]]:
    """Compile a SQLAlchemy Core statement for a specific dialect.

    Returns ``(sql_string, params_tuple)`` ready for
    ``backend.execute(sql, params)`` or ``backend.fetch_*(sql, params)``.

    Parameters
    ----------
    stmt:
        A SQLAlchemy Core statement (``select()``, ``insert()``, etc.).
    dialect:
        One of ``"sqlite"``, ``"postgres"``, ``"postgresql"``.

    Returns
    -------
    tuple:
        ``(sql_string, params_tuple)`` with dialect-correct placeholders.
    """
    sa_dialect = _DIALECTS.get(dialect)
    if sa_dialect is None:
        raise ValueError(
            f"Unsupported dialect: {dialect!r}. "
            f"Supported: {sorted(_DIALECTS)}"
        )

    compiled = stmt.compile(
        dialect=sa_dialect,
        compile_kwargs={"literal_binds": False, "render_postcompile": True},
    )
    sql = str(compiled)

    if compiled.positiontup:
        # SQLite dialect: positional ? placeholders with positiontup ordering
        params = tuple(compiled.params[k] for k in compiled.positiontup)
    elif compiled.params:
        # Postgres dialect: named %(key)s placeholders, no positiontup.
        # Convert to positional ? placeholders (pyscoped's StorageBackend
        # uses ? universally; translate_placeholders converts to %s for PG).
        import re

        ordered_params: list[Any] = []
        def _replace_named(m: re.Match) -> str:
            key = m.group(1)
            ordered_params.append(compiled.params[key])
            return "?"

        sql = re.sub(r"%\((\w+)\)s", _replace_named, sql)
        params = tuple(ordered_params)
    else:
        params = ()

    return sql, params
