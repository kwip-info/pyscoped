# A8: Storage Tiering / Archival

**Extends:** Storage

## Purpose

Old versions, completed environments, and archived scopes accumulate. Storage tiering moves data between hot, warm, cold, and glacial tiers based on retention policies. Glacial archives are compressed, sealed, and integrity-verified bundles for long-term storage.

## Core Concepts

### StorageTier

Four tiers with increasing coldness:

| Tier | Rank | Purpose |
|------|------|---------|
| `HOT` | 0 | Active, frequently accessed data |
| `WARM` | 1 | Recent but less frequently accessed |
| `COLD` | 2 | Old data, infrequent access |
| `GLACIAL` | 3 | Compressed, sealed, long-term archives |

### TierAssignment

Tracks which tier an object version currently lives in.

| Field | Purpose |
|-------|---------|
| `object_id` | Which object |
| `version` | Which version |
| `tier` | Current storage tier |
| `previous_tier` | Where it came from (nullable for initial assignment) |
| `assigned_by` | Which principal or policy triggered the move |

### RetentionPolicy

Rule-based policies that automatically identify candidates for tier transitions.

| Field | Purpose |
|-------|---------|
| `name` | Human-readable label |
| `source_tier` | Current tier to match (e.g., HOT) |
| `target_tier` | Tier to move to (e.g., WARM) — must be colder than source |
| `condition_type` | `age_days` or `lifecycle_state` |
| `condition_value` | Days old, or lifecycle state to match (e.g., "ARCHIVED") |
| `object_type` | Optional — restrict to specific object types |
| `scope_id` | Optional — restrict to specific scopes |

### TierManager

Service layer for tier operations:

- `assign_tier()` — assign or change an object version's tier
- `get_tier()` / `get_object_tiers()` — query current assignments
- `create_policy()` — create a retention policy (validates target > source)
- `evaluate_policies()` — scan for transition candidates
- `apply_transitions()` — execute pending tier moves

### GlacialArchive

A compressed, integrity-verified bundle of object versions.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `name` | Human-readable label |
| `object_ids` | List of archived object IDs |
| `sealed` | Whether the archive is immutable |
| `content_hash` | SHA-256 of compressed content |
| `compressed_size` | Size after gzip compression |
| `original_size` | Size before compression |
| `entry_count` | Total number of version entries |

### ArchiveManager

Service layer for glacial archives:

- `create_archive(object_ids, owner_id)` — collect all versions, gzip compress, compute hash
- `seal_archive(archive_id)` — make immutable (cannot be deleted or modified after sealing)
- `extract_archive(archive_id)` — decompress and return entries (verifies integrity)
- `verify_archive(archive_id)` — check content hash matches stored data
- `delete_archive(archive_id)` — only works on unsealed archives

## Schema

```sql
CREATE TABLE tier_assignments (
    id              TEXT PRIMARY KEY,
    object_id       TEXT NOT NULL REFERENCES scoped_objects(id),
    version         INTEGER NOT NULL,
    tier            TEXT NOT NULL,
    assigned_at     TEXT NOT NULL,
    assigned_by     TEXT NOT NULL DEFAULT 'system',
    previous_tier   TEXT,
    UNIQUE(object_id, version)
);

CREATE TABLE retention_policies (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    source_tier     TEXT NOT NULL,
    target_tier     TEXT NOT NULL,
    condition_type  TEXT NOT NULL,
    condition_value TEXT NOT NULL,
    object_type     TEXT,
    scope_id        TEXT REFERENCES scopes(id),
    owner_id        TEXT NOT NULL REFERENCES principals(id),
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE glacial_archives (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    object_ids_json TEXT NOT NULL,
    owner_id        TEXT NOT NULL REFERENCES principals(id),
    created_at      TEXT NOT NULL,
    sealed          INTEGER NOT NULL DEFAULT 0,
    sealed_at       TEXT,
    content_hash    TEXT NOT NULL,
    compressed_data BLOB NOT NULL,
    compressed_size INTEGER NOT NULL,
    original_size   INTEGER NOT NULL,
    entry_count     INTEGER NOT NULL DEFAULT 0,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE'
);
```

## Files

```
scoped/storage/
    tiering.py         # StorageTier, TierAssignment, TierManager, RetentionPolicy
    archival.py        # GlacialArchive, ArchiveEntry, ArchiveManager
```

## Usage

```python
from scoped.storage.tiering import TierManager, StorageTier
from scoped.storage.archival import ArchiveManager

# Tier management
tier_mgr = TierManager(backend)
tier_mgr.assign_tier(object_id, version=1, tier=StorageTier.HOT, assigned_by=user_id)

# Create a retention policy
tier_mgr.create_policy(
    name="Archive old versions",
    source_tier=StorageTier.HOT,
    target_tier=StorageTier.COLD,
    condition_type="age_days",
    condition_value="90",
    owner_id=admin_id,
)

# Evaluate and apply
candidates = tier_mgr.evaluate_policies()
tier_mgr.apply_transitions(candidates, applied_by=admin_id)

# Glacial archival
archive_mgr = ArchiveManager(backend)
archive = archive_mgr.create_archive(object_ids=[obj1, obj2], owner_id=admin_id)
archive_mgr.seal_archive(archive.id)
assert archive_mgr.verify_archive(archive.id)
```

## Invariants

1. Target tier must be colder than source tier (higher rank).
2. Sealed archives cannot be deleted or modified.
3. Archive integrity is verified by SHA-256 hash on every extract.
4. Retention policies only move data to colder tiers, never warmer.
5. Glacial archives use gzip compression for space efficiency.
