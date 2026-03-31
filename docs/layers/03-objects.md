# Layer 3: Object Versioning & Isolation

## Purpose

Objects are the data in the system. Every piece of information managed by Scoped — a document, a record, a configuration, a result — is a **scoped object**. This layer enforces two absolute rules:

1. **Every mutation creates a new version.** There are no in-place updates. The complete history of every object is preserved.
2. **Every object starts completely isolated.** Only the creator can see it until they explicitly share it.

## Core Concepts

### ScopedObject

The envelope around any data. A `ScopedObject` doesn't contain the data itself — it contains the identity, ownership, and versioning metadata.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `object_type` | What kind of data this is (model name, registry kind) |
| `owner_id` | The principal who created it — the ONLY one who can see it by default |
| `current_version` | Latest version number |
| `lifecycle` | ACTIVE, ARCHIVED (tombstoned) |

### ObjectVersion

An immutable snapshot of the object's state at a point in time.

| Field | Purpose |
|-------|---------|
| `object_id` | Which object this is a version of |
| `version` | Sequential version number |
| `data_json` | The serialized object state at this version |
| `created_by` | Which principal created this version |
| `change_reason` | Why this version was created |
| `checksum` | Integrity hash of the data |

Every `save()` creates a new `ObjectVersion`. The previous version is never modified. This is what makes rollback possible (Layer 7) and what gives the audit trail (Layer 6) its before/after states.

### Tombstone

When an object is "deleted," it's actually tombstoned:

| Field | Purpose |
|-------|---------|
| `object_id` | The tombstoned object |
| `tombstoned_by` | Who did it |
| `reason` | Why |

The object and all its versions remain in storage. The tombstone marks it as no longer active. This can be reversed by rollback.

### Isolation

Default visibility is **creator-only**. When a new object is created:
- The `owner_id` is set to the acting principal from `ScopedContext`
- No scopes, no projections, no memberships — just the owner
- The owner must explicitly project the object into a scope (Layer 4) for anyone else to see it

The `ScopedManager` — the custom queryset manager that replaces Django's default — automatically filters all queries to respect isolation. If you query for objects, you only see:
1. Objects you own
2. Objects projected into scopes you're a member of

There is no `objects.all()` that returns everything. That concept doesn't exist in Scoped.

## How It Connects

### To Layer 1 (Registry)
Every scoped object has a `registry_entry_id`. Object types are resolved through the registry.

### To Layer 2 (Identity)
Every object has an `owner_id` (a principal). Every version has a `created_by` (a principal). Isolation is enforced against the acting principal from `ScopedContext`.

### To Layer 4 (Tenancy)
Sharing happens via scope projections. An object is projected into a scope, making it visible to scope members. Without a projection, the object is invisible to everyone except the owner.

### To Layer 6 (Audit)
Every object operation — create, read, update, tombstone — produces a trace entry with before/after state (serialized from object versions).

### To Layer 7 (Temporal)
Object versioning is what makes temporal reconstruction possible. "Show me this object at timestamp T" means "find the version that was current at T."

### To Layer 8 (Environments)
Objects created within an environment are tracked in `environment_objects`. They inherit the environment's isolation boundary. When the environment is discarded, its objects can be discarded too. When promoted, specific objects are projected into persistent scopes.

### To Layer 11 (Secrets)
Secrets are scoped objects — they have an `object_id` linking to `scoped_objects`. This means secrets get versioning, isolation, and lifecycle for free. The secrets layer adds encryption on top.

## Extensions

This layer has been extended with:

- **[A4: Blob / Media Storage](../extensions/A4-blobs.md)** — Binary content (files, images, documents) with the same versioning, isolation, and audit guarantees as JSON objects. Content-addressed by SHA-256 hash.
- **[A6: Search / Indexing](../extensions/A6-search.md)** — Scope-aware full-text search over object data using SQLite FTS5. Find objects by content, not just by ID or type.
- **[A9: Data Import / Export](../extensions/A9-import-export.md)** — Portable JSON packages for bulk data movement. Exports respect isolation; imports create new objects with new IDs and proper ownership.

## Files

```
scoped/objects/
    __init__.py
    models.py        # ScopedObject, ObjectVersion, Tombstone
    manager.py       # ScopedManager — isolation-enforcing queryset
    versioning.py    # Create version on save, diff between versions
    isolation.py     # Isolation boundary enforcement
    blobs.py         # [A4] BlobRef, BlobVersion, BlobManager
    search.py        # [A6] SearchIndex, IndexEntry, SearchResult
    export.py        # [A9] ExportPackage, Exporter
    import_.py       # [A9] ImportResult, Importer
```

## Schema

```sql
CREATE TABLE scoped_objects (
    id              TEXT PRIMARY KEY,
    object_type     TEXT NOT NULL,
    owner_id        TEXT NOT NULL REFERENCES principals(id),
    registry_entry_id TEXT,
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE object_versions (
    id              TEXT PRIMARY KEY,
    object_id       TEXT NOT NULL REFERENCES scoped_objects(id),
    version         INTEGER NOT NULL,
    data_json       TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    change_reason   TEXT NOT NULL DEFAULT '',
    checksum        TEXT NOT NULL DEFAULT '',
    UNIQUE(object_id, version)
);

CREATE TABLE tombstones (
    id              TEXT PRIMARY KEY,
    object_id       TEXT NOT NULL UNIQUE REFERENCES scoped_objects(id),
    tombstoned_at   TEXT NOT NULL,
    tombstoned_by   TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT ''
);
```

## Invariants

1. Every mutation creates a new version. No in-place updates ever.
2. Default visibility is creator-only. No implicit sharing.
3. Objects are never physically deleted. Only tombstoned.
4. All object versions are retained for audit and rollback.
5. The `ScopedManager` filters all queries to respect isolation boundaries.
