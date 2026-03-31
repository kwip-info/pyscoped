# A6: Search / Indexing

**Extends:** Layer 3 (Objects)

## Purpose

Finding objects by content, not just by ID or type. The search index provides scope-aware full-text search over object data and metadata, respecting isolation boundaries.

## Core Concepts

### SearchIndex

A scope-aware full-text search index over object data. Built on SQLite FTS5 for the default backend.

### IndexEntry

A single indexed field from an object.

| Field | Purpose |
|-------|---------|
| `object_id` | Which object this entry indexes |
| `object_type` | The type of the object |
| `field_name` | Which field within the object data |
| `content` | The indexed text content |
| `scope_id` | Which scope this entry is visible in (nullable = owner-only) |

### SearchResult

A search hit with relevance metadata.

| Field | Purpose |
|-------|---------|
| `object_id` | The matching object |
| `object_type` | Type of the matching object |
| `field_name` | Which field matched |
| `snippet` | Matched text excerpt |
| `rank` | Relevance score (lower = better match) |

### Operations

- `index_object(object_id, data, object_type, scope_id)` — index all string fields
- `remove_object(object_id)` — remove all index entries
- `search(query, scope_id, object_type, limit)` — full-text search with filters
- `reindex_object(object_id, data, object_type, scope_id)` — remove + re-index

## Schema

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    object_id,
    object_type,
    field_name,
    content,
    scope_id UNINDEXED
);
```

## Files

```
scoped/objects/
    search.py          # SearchIndex, IndexEntry, SearchResult
```

## Usage

```python
from scoped.objects.search import SearchIndex

index = SearchIndex(backend)

# Index an object
index.index_object(
    object_id=obj_id,
    data={"title": "Quarterly Report", "body": "Revenue grew 15% ..."},
    object_type="document",
    scope_id=team_scope_id,
)

# Search within a scope
results = index.search("quarterly revenue", scope_id=team_scope_id)
for r in results:
    print(f"{r.object_id}: {r.snippet} (rank: {r.rank})")
```

## Invariants

1. Search results respect scope isolation — you only find objects in scopes you can see.
2. Indexing is explicit — objects are indexed by the application, not automatically.
3. FTS5 provides relevance ranking — results are ordered by match quality.
4. Removing an object from the index removes all its field entries.
