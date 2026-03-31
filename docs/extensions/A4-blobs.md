# A4: Blob / Media Storage

**Extends:** Layer 3 (Objects) + Storage

## Purpose

Objects store `data_json` — structured data. But applications also need binary content: files, images, documents, archives. Blobs bring the same isolation, versioning, and audit guarantees to binary content.

## Core Concepts

### BlobRef

A reference to binary content stored in a blob backend. Blobs are content-addressed by SHA-256 hash.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `content_hash` | SHA-256 of the content — serves as deduplication key |
| `size_bytes` | Size of the stored content |
| `content_type` | MIME type (e.g., `image/png`, `application/pdf`) |
| `backend_path` | Where the blob is physically stored |
| `created_at` | When the blob was created |
| `created_by` | Which principal created it |

### BlobVersion

A versioned binary content entry tied to a scoped object. When an object's binary content changes, a new BlobVersion is created.

| Field | Purpose |
|-------|---------|
| `blob_ref_id` | Which blob this version points to |
| `object_id` | Which scoped object this belongs to |
| `version` | Sequential version number |

### BlobBackend

Pluggable interface for where blobs are physically stored:

- **InMemoryBlobBackend** — for tests
- **LocalBlobBackend** — local filesystem storage (configurable root directory)
- Extensible to S3-compatible, GCS, Azure Blob, etc.

### BlobManager

Service layer that ties it all together: store content, create refs, create versions, read content back. All operations go through the manager for audit and isolation.

## Schema

```sql
CREATE TABLE blobs (
    id              TEXT PRIMARY KEY,
    content_hash    TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    content_type    TEXT NOT NULL DEFAULT 'application/octet-stream',
    backend_path    TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL DEFAULT 'system'
);

CREATE TABLE blob_versions (
    id              TEXT PRIMARY KEY,
    blob_ref_id     TEXT NOT NULL REFERENCES blobs(id),
    object_id       TEXT NOT NULL REFERENCES scoped_objects(id),
    version         INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(object_id, version)
);
```

## Files

```
scoped/storage/
    blobs.py           # BlobBackend interface, InMemoryBlobBackend, LocalBlobBackend
scoped/objects/
    blobs.py           # BlobRef, BlobVersion, BlobManager
```

## Usage

```python
from scoped.objects.blobs import BlobManager

mgr = BlobManager(backend, blob_backend)

# Store binary content
ref = mgr.store_blob(
    content=b"file contents here",
    object_id=my_object_id,
    content_type="text/plain",
    principal_id=user_id,
)

# Read it back
content = mgr.read_blob(ref.id)

# Get version history
versions = mgr.get_blob_versions(my_object_id)
```

## Invariants

1. Blobs are content-addressed — identical content produces identical hashes.
2. Blob visibility follows the parent object's isolation boundary.
3. Blob operations (create, read, delete) produce their own ActionTypes.
4. BlobBackend is pluggable — the storage layer doesn't dictate where blobs live.
