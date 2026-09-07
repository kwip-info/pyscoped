# A9: Data Import / Export

**Extends:** Layer 3 (Objects)

## Purpose

Bulk data movement into and out of the framework. Essential for onboarding, backup, migration between Scoped instances, and interoperability. Exports produce self-contained portable packages; imports create new objects with proper isolation and audit trail.

## Core Concepts

### ExportPackage

A self-contained, JSON-serializable bundle of objects and their complete version histories.

| Component | Purpose |
|-----------|---------|
| `ExportManifest` | Metadata: format version, timestamp, exporter, object/version counts |
| `ExportedObject` | One object with its type, owner, lifecycle, and full version list |
| `ExportedVersion` | One version snapshot: version number, data, author, checksum |

### Exporter

Creates `ExportPackage` instances from live objects. Respects isolation:

- `export_object(object_id, principal_id)` — export a single object (must be owned by principal)
- `export_objects(object_ids, principal_id)` — export multiple (silently skips non-owned)
- `export_by_type(object_type, principal_id)` — export all objects of a type owned by principal

Only objects owned by the exporting principal are included. Objects not found or not owned are silently skipped.

### Importer

Ingests an `ExportPackage` and creates new objects:

- `import_package(package, principal_id)` — import all objects from a package
- `import_from_dict(data, principal_id)` — convenience wrapper for raw dicts
- `import_from_json(raw, principal_id)` — convenience wrapper for JSON strings

**Import behavior:**
1. Each imported object gets a **new ID** (never reuses the original)
2. The **importing principal** becomes the owner (not the original owner)
3. All versions are recreated with the new object ID
4. Checksums are **recomputed by default** to verify data integrity
5. The original-to-new ID mapping is returned in `ImportResult.id_mapping`

### ImportResult

| Field | Purpose |
|-------|---------|
| `imported_count` | Number of objects successfully imported |
| `skipped_count` | Number of objects skipped (filtered or errored) |
| `version_count` | Total number of versions created |
| `id_mapping` | Dict mapping old object IDs to new object IDs |
| `errors` | List of error messages for failed imports |

### Filtering

- `object_type_filter` — only import objects of a specific type (others are skipped)
- `recompute_checksums` — set to `False` to trust the exported checksums

## Serialization

The package format is a plain JSON structure:

```json
{
    "manifest": {
        "format_version": "1.0",
        "exported_at": "2026-03-26T12:00:00+00:00",
        "exported_by": "principal-id",
        "object_count": 2,
        "version_count": 5
    },
    "objects": [
        {
            "id": "original-object-id",
            "object_type": "document",
            "owner_id": "original-owner",
            "created_at": "...",
            "lifecycle": "ACTIVE",
            "versions": [
                {
                    "version": 1,
                    "data": {"title": "hello"},
                    "created_at": "...",
                    "created_by": "...",
                    "change_reason": "created",
                    "checksum": "sha256hex..."
                }
            ]
        }
    ]
}
```

## Files

```
scoped/objects/
    export.py          # ExportPackage, ExportManifest, ExportedObject, ExportedVersion, Exporter
    import_.py         # ImportResult, Importer (underscore avoids Python keyword)
```

## Usage

```python
from scoped.objects.export import Exporter
from scoped.objects.import_ import Importer

# Export
exporter = Exporter(backend)
pkg = exporter.export_by_type("document", principal_id=alice_id)
json_str = pkg.to_json(indent=2)

# Transfer json_str to another system...

# Import
importer = Importer(backend)
result = importer.import_from_json(json_str, principal_id=bob_id)
print(f"Imported {result.imported_count} objects, {result.version_count} versions")
print(f"ID mapping: {result.id_mapping}")
```

## Invariants

1. Exports only include objects owned by the exporting principal.
2. Imports always create new objects with new IDs — never overwrites existing.
3. The importing principal becomes the owner of all imported objects.
4. Checksums are recomputed by default to verify data integrity on import.
5. Importing the same package twice creates distinct objects (idempotency is not guaranteed).
6. The `import_.py` filename uses an underscore suffix to avoid colliding with Python's `import` keyword.
