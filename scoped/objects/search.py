"""Scope-aware full-text search over object data and metadata.

Uses pluggable strategies for the full-text index backend:
- SQLite FTS5 (default)
- PostgreSQL tsvector / tsquery

Index entries are created/updated when objects are indexed. Search results
are filtered by the caller's visibility (owner-only at Layer 3, scope-aware
when combined with Layer 4's VisibilityEngine).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from scoped.objects._search_strategies import (
    PostgresFTSStrategy,
    SearchStrategy,
    SQLiteFTS5Strategy,
)
from scoped.storage.interface import StorageBackend
from scoped.types import generate_id, now_utc


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SearchResult:
    """A single search hit."""

    object_id: str
    object_type: str
    owner_id: str
    field_name: str
    snippet: str
    rank: float


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """A searchable index entry for one field of one object."""

    id: str
    object_id: str
    object_type: str
    owner_id: str
    field_name: str
    content: str
    scope_id: str | None
    indexed_at: datetime


def index_entry_from_row(row: dict[str, Any]) -> IndexEntry:
    """Convert a database row to an IndexEntry."""
    return IndexEntry(
        id=row["id"],
        object_id=row["object_id"],
        object_type=row["object_type"],
        owner_id=row["owner_id"],
        field_name=row["field_name"],
        content=row["content"],
        scope_id=row.get("scope_id"),
        indexed_at=datetime.fromisoformat(row["indexed_at"]),
    )


# ---------------------------------------------------------------------------
# SearchIndex — indexing and querying
# ---------------------------------------------------------------------------

class SearchIndex:
    """Scope-aware full-text search index.

    Indexes object data fields for efficient full-text queries.
    Results are filtered by principal visibility.
    """

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend
        self._strategy: SearchStrategy = (
            PostgresFTSStrategy()
            if backend.dialect == "postgres"
            else SQLiteFTS5Strategy()
        )

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_object(
        self,
        *,
        object_id: str,
        object_type: str,
        owner_id: str,
        data: dict[str, Any],
        scope_id: str | None = None,
        fields: list[str] | None = None,
    ) -> int:
        """Index an object's data fields.

        If `fields` is given, only index those keys. Otherwise index all
        top-level string values. Returns count of fields indexed.

        Re-indexing the same object replaces old entries.
        """
        # Remove existing index entries for this object
        self._strategy.remove_entries(self._backend, object_id)

        ts = now_utc()
        count = 0

        keys_to_index = fields if fields is not None else list(data.keys())

        for key in keys_to_index:
            value = data.get(key)
            if value is None:
                continue

            # Convert to searchable text
            text = self._to_text(value)
            if not text:
                continue

            entry_id = generate_id()
            # Insert into metadata table
            self._backend.execute(
                "INSERT INTO search_index "
                "(id, object_id, object_type, owner_id, field_name, content, scope_id, indexed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry_id, object_id, object_type, owner_id,
                    key, text, scope_id, ts.isoformat(),
                ),
            )
            # Populate FTS index via strategy
            self._strategy.index_entry(self._backend, entry_id, text)
            count += 1

        return count

    def remove_object(self, object_id: str) -> int:
        """Remove all index entries for an object. Returns count removed."""
        entries = self._backend.fetch_all(
            "SELECT id FROM search_index WHERE object_id = ?",
            (object_id,),
        )
        self._strategy.remove_entries(self._backend, object_id)
        return len(entries)

    def reindex_object(
        self,
        *,
        object_id: str,
        object_type: str,
        owner_id: str,
        data: dict[str, Any],
        scope_id: str | None = None,
        fields: list[str] | None = None,
    ) -> int:
        """Re-index an object (convenience alias for index_object)."""
        return self.index_object(
            object_id=object_id,
            object_type=object_type,
            owner_id=owner_id,
            data=data,
            scope_id=scope_id,
            fields=fields,
        )

    # ------------------------------------------------------------------
    # Searching
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        principal_id: str,
        object_type: str | None = None,
        scope_id: str | None = None,
        limit: int = 50,
    ) -> list[SearchResult]:
        """Full-text search filtered by principal visibility.

        At Layer 3, visibility is owner-only. To include scope-based
        visibility, combine with VisibilityEngine.

        Returns results ranked by relevance.
        """
        if not query.strip():
            return []

        clauses = ["si.owner_id = ?"]
        params: list[Any] = [principal_id]

        if object_type is not None:
            clauses.append("si.object_type = ?")
            params.append(object_type)

        if scope_id is not None:
            clauses.append("si.scope_id = ?")
            params.append(scope_id)

        where = " AND ".join(clauses)

        sql, final_params = self._strategy.build_search_sql(query, where, params, limit)
        rows = self._backend.fetch_all(sql, final_params)

        return [
            SearchResult(
                object_id=row["object_id"],
                object_type=row["object_type"],
                owner_id=row["owner_id"],
                field_name=row["field_name"],
                snippet=row["content"][:200],
                rank=row["rank"],
            )
            for row in rows
        ]

    def search_with_visibility(
        self,
        query: str,
        *,
        principal_id: str,
        visible_object_ids: list[str],
        object_type: str | None = None,
        limit: int = 50,
    ) -> list[SearchResult]:
        """Full-text search filtered by an explicit visibility set.

        Use this with VisibilityEngine.visible_object_ids() for
        scope-aware search results.
        """
        if not query.strip() or not visible_object_ids:
            return []

        placeholders = ",".join("?" for _ in visible_object_ids)
        clauses = [f"si.object_id IN ({placeholders})"]
        params: list[Any] = list(visible_object_ids)

        if object_type is not None:
            clauses.append("si.object_type = ?")
            params.append(object_type)

        where = " AND ".join(clauses)

        sql, final_params = self._strategy.build_search_sql(query, where, params, limit)
        rows = self._backend.fetch_all(sql, final_params)

        return [
            SearchResult(
                object_id=row["object_id"],
                object_type=row["object_type"],
                owner_id=row["owner_id"],
                field_name=row["field_name"],
                snippet=row["content"][:200],
                rank=row["rank"],
            )
            for row in rows
        ]

    def count_results(
        self,
        query: str,
        *,
        principal_id: str,
        object_type: str | None = None,
        scope_id: str | None = None,
    ) -> int:
        """Count search results without fetching them."""
        if not query.strip():
            return 0

        clauses = ["si.owner_id = ?"]
        params: list[Any] = [principal_id]

        if object_type is not None:
            clauses.append("si.object_type = ?")
            params.append(object_type)

        if scope_id is not None:
            clauses.append("si.scope_id = ?")
            params.append(scope_id)

        where = " AND ".join(clauses)

        sql, final_params = self._strategy.build_count_sql(query, where, params)
        row = self._backend.fetch_one(sql, final_params)
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Index introspection
    # ------------------------------------------------------------------

    def get_indexed_fields(self, object_id: str) -> list[str]:
        """Get list of field names indexed for an object."""
        rows = self._backend.fetch_all(
            "SELECT field_name FROM search_index WHERE object_id = ? ORDER BY field_name",
            (object_id,),
        )
        return [r["field_name"] for r in rows]

    def is_indexed(self, object_id: str) -> bool:
        """Check if an object has any index entries."""
        row = self._backend.fetch_one(
            "SELECT 1 FROM search_index WHERE object_id = ? LIMIT 1",
            (object_id,),
        )
        return row is not None

    def index_count(self) -> int:
        """Total number of index entries."""
        row = self._backend.fetch_one("SELECT COUNT(*) as cnt FROM search_index", ())
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_text(value: Any) -> str:
        """Convert a value to searchable text."""
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, list):
            return " ".join(str(v) for v in value if v is not None)
        if isinstance(value, dict):
            return " ".join(str(v) for v in value.values() if v is not None)
        return str(value)
