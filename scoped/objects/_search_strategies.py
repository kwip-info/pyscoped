"""FTS backend strategies for SearchIndex.

SQLite uses FTS5 virtual tables with rowid-based joins.
PostgreSQL uses tsvector columns with GIN indexes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

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
        row = backend.fetch_one(
            "SELECT rowid FROM search_index WHERE id = ?", (entry_id,)
        )
        rowid = row["rowid"]
        backend.execute(
            "INSERT INTO search_index_fts (rowid, content) VALUES (?, ?)",
            (rowid, content),
        )

    def remove_entries(self, backend: StorageBackend, object_id: str) -> None:
        entries = backend.fetch_all(
            "SELECT rowid FROM search_index WHERE object_id = ?", (object_id,)
        )
        for entry in entries:
            backend.execute(
                "DELETE FROM search_index_fts WHERE rowid = ?",
                (entry["rowid"],),
            )
        backend.execute(
            "DELETE FROM search_index WHERE object_id = ?", (object_id,)
        )

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
        backend.execute(
            "DELETE FROM search_index WHERE object_id = ?", (object_id,)
        )

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
