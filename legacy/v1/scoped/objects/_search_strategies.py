"""FTS backend strategies for SearchIndex.

SQLite uses FTS5 virtual tables with rowid-based joins.
PostgreSQL uses tsvector columns with GIN indexes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import sqlalchemy as sa

from scoped.storage._query import compile_for
from scoped.storage._schema import search_index
from scoped.storage.interface import StorageBackend


class SearchStrategy(ABC):
    """Abstract strategy for full-text search operations."""

    @abstractmethod
    def index_entry(
        self, backend: StorageBackend, entry_id: str, content: str
    ) -> None:
        """Populate the FTS index for a newly inserted search_index row."""
        ...

    @abstractmethod
    def remove_entries(self, backend: StorageBackend, object_id: str) -> None:
        """Remove FTS data for all entries belonging to *object_id*."""
        ...

    @abstractmethod
    def build_search_sql(
        self,
        query: str,
        where: str,
        params: list[Any],
        limit: int,
    ) -> tuple[str, tuple[Any, ...]]:
        """Return ``(sql, params)`` for a ranked full-text search."""
        ...

    @abstractmethod
    def build_count_sql(
        self,
        query: str,
        where: str,
        params: list[Any],
    ) -> tuple[str, tuple[Any, ...]]:
        """Return ``(sql, params)`` that counts matching rows."""
        ...


class SQLiteFTS5Strategy(SearchStrategy):
    """FTS5 virtual-table strategy for SQLite."""

    def index_entry(
        self, backend: StorageBackend, entry_id: str, content: str
    ) -> None:
        # rowid lookup uses SA Core; FTS INSERT stays raw (virtual table)
        stmt = sa.select(sa.literal_column("rowid")).select_from(
            search_index,
        ).where(search_index.c.id == entry_id)
        sql, params = compile_for(stmt, backend.dialect)
        row = backend.fetch_one(sql, params)
        rowid = row["rowid"]
        backend.execute(
            "INSERT INTO search_index_fts (rowid, content) VALUES (?, ?)",
            (rowid, content),
        )

    def remove_entries(self, backend: StorageBackend, object_id: str) -> None:
        sel_stmt = sa.select(sa.literal_column("rowid")).select_from(
            search_index,
        ).where(search_index.c.object_id == object_id)
        sql, params = compile_for(sel_stmt, backend.dialect)
        entries = backend.fetch_all(sql, params)
        for entry in entries:
            # FTS virtual table delete stays raw
            backend.execute(
                "DELETE FROM search_index_fts WHERE rowid = ?",
                (entry["rowid"],),
            )
        del_stmt = sa.delete(search_index).where(
            search_index.c.object_id == object_id,
        )
        sql, params = compile_for(del_stmt, backend.dialect)
        backend.execute(sql, params)

    def build_search_sql(
        self,
        query: str,
        where: str,
        params: list[Any],
        limit: int,
    ) -> tuple[str, tuple[Any, ...]]:
        sql = (
            "SELECT si.object_id, si.object_type, si.owner_id, si.field_name, "
            "si.content, fts.rank "
            "FROM search_index_fts fts "
            "JOIN search_index si ON si.rowid = fts.rowid "
            f"WHERE fts.content MATCH ? AND {where} "
            "ORDER BY fts.rank "
            "LIMIT ?"
        )
        return sql, (query, *params, limit)

    def build_count_sql(
        self,
        query: str,
        where: str,
        params: list[Any],
    ) -> tuple[str, tuple[Any, ...]]:
        sql = (
            "SELECT COUNT(*) as cnt "
            "FROM search_index_fts fts "
            "JOIN search_index si ON si.rowid = fts.rowid "
            f"WHERE fts.content MATCH ? AND {where}"
        )
        return sql, (query, *params)


class PostgresFTSStrategy(SearchStrategy):
    """tsvector / tsquery strategy for PostgreSQL."""

    def index_entry(
        self, backend: StorageBackend, entry_id: str, content: str
    ) -> None:
        backend.execute(
            "UPDATE search_index SET search_vector = to_tsvector('english', ?) "
            "WHERE id = ?",
            (content, entry_id),
        )

    def remove_entries(self, backend: StorageBackend, object_id: str) -> None:
        stmt = sa.delete(search_index).where(
            search_index.c.object_id == object_id,
        )
        sql, params = compile_for(stmt, backend.dialect)
        backend.execute(sql, params)

    def build_search_sql(
        self,
        query: str,
        where: str,
        params: list[Any],
        limit: int,
    ) -> tuple[str, tuple[Any, ...]]:
        sql = (
            "SELECT si.object_id, si.object_type, si.owner_id, si.field_name, "
            "si.content, ts_rank(si.search_vector, plainto_tsquery('english', ?)) AS rank "
            "FROM search_index si "
            f"WHERE si.search_vector @@ plainto_tsquery('english', ?) AND {where} "
            "ORDER BY rank DESC "
            "LIMIT ?"
        )
        return sql, (query, query, *params, limit)

    def build_count_sql(
        self,
        query: str,
        where: str,
        params: list[Any],
    ) -> tuple[str, tuple[Any, ...]]:
        sql = (
            "SELECT COUNT(*) as cnt "
            "FROM search_index si "
            f"WHERE si.search_vector @@ plainto_tsquery('english', ?) AND {where}"
        )
        return sql, (query, *params)
