"""Import objects from an ExportPackage into the system.

Imports create new objects with new IDs. The original IDs are preserved
in the ID mapping so callers can correlate old and new. All imported
objects are owned by the importing principal.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from scoped.objects.export import (
    ExportPackage,
    ExportedObject,
    ExportedVersion,
    FORMAT_VERSION,
)
from scoped.objects.models import compute_checksum
from scoped.storage._query import compile_for
from scoped.storage._schema import object_versions, scoped_objects
from scoped.storage.interface import StorageBackend
from scoped.types import Lifecycle, generate_id, now_utc


# ---------------------------------------------------------------------------
# Import result
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ImportResult:
    """Result of an import operation."""

    imported_count: int = 0
    skipped_count: int = 0
    version_count: int = 0
    id_mapping: dict[str, str] = field(default_factory=dict)  # old_id -> new_id
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Importer
# ---------------------------------------------------------------------------

def _parse_exported_version(v: dict[str, Any]) -> ExportedVersion:
    """Parse a raw version dict into an ExportedVersion."""
    return ExportedVersion(
        version=v["version"],
        data=v["data"],
        created_at=v["created_at"],
        created_by=v["created_by"],
        change_reason=v.get("change_reason", ""),
        checksum=v["checksum"],
    )


class Importer:
    """Imports objects from an ExportPackage.

    All imported objects get new IDs and are owned by the importing
    principal. Version history is preserved. Checksums are revalidated
    on import.
    """

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def import_package(
        self,
        package: ExportPackage,
        *,
        principal_id: str,
        object_type_filter: str | None = None,
        recompute_checksums: bool = True,
    ) -> ImportResult:
        """Import all objects from a package.

        Args:
            package: The export package to import.
            principal_id: The principal who will own the imported objects.
            object_type_filter: If set, only import objects of this type.
            recompute_checksums: If True, recompute checksums on import
                to verify data integrity.

        Returns:
            ImportResult with counts and ID mapping.
        """
        result = ImportResult()

        for exported_obj in package.objects:
            if object_type_filter and exported_obj.object_type != object_type_filter:
                result.skipped_count += 1
                continue

            try:
                new_id = self._import_object(
                    exported_obj,
                    principal_id=principal_id,
                    recompute_checksums=recompute_checksums,
                )
                result.id_mapping[exported_obj.id] = new_id
                result.imported_count += 1
                result.version_count += len(exported_obj.versions)
            except Exception as exc:
                result.errors.append(
                    f"Failed to import object {exported_obj.id}: {exc}"
                )
                result.skipped_count += 1

        return result

    def import_from_dict(
        self,
        data: dict[str, Any],
        *,
        principal_id: str,
        **kwargs: Any,
    ) -> ImportResult:
        """Import from a raw dict (convenience wrapper)."""
        package = ExportPackage.from_dict(data)
        return self.import_package(package, principal_id=principal_id, **kwargs)

    def import_from_json(
        self,
        raw: str,
        *,
        principal_id: str,
        **kwargs: Any,
    ) -> ImportResult:
        """Import from a JSON string (convenience wrapper)."""
        package = ExportPackage.from_json(raw)
        return self.import_package(package, principal_id=principal_id, **kwargs)

    def stream_import(
        self,
        lines: Iterator[str],
        *,
        principal_id: str,
        object_type_filter: str | None = None,
        recompute_checksums: bool = True,
    ) -> ImportResult:
        """Import objects from a stream of NDJSON lines.

        Consumes lines one at a time.  The first line with
        ``_type: "manifest"`` is recognized and skipped.  Each
        subsequent line is a JSON object matching the export format.

        Memory usage is O(single_object) not O(all_objects).
        """
        result = ImportResult()

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                result.errors.append(f"Invalid JSON line: {exc}")
                result.skipped_count += 1
                continue

            # Skip manifest lines
            if data.get("_type") == "manifest":
                continue

            # Parse as ExportedObject
            if object_type_filter and data.get("object_type") != object_type_filter:
                result.skipped_count += 1
                continue

            try:
                exported = ExportedObject(
                    id=data["id"],
                    object_type=data["object_type"],
                    owner_id=data["owner_id"],
                    created_at=data["created_at"],
                    lifecycle=data["lifecycle"],
                    versions=[
                        _parse_exported_version(v)
                        for v in data.get("versions", [])
                    ],
                )
                new_id = self._import_object(
                    exported,
                    principal_id=principal_id,
                    recompute_checksums=recompute_checksums,
                )
                result.id_mapping[exported.id] = new_id
                result.imported_count += 1
                result.version_count += len(exported.versions)
            except Exception as exc:
                result.errors.append(
                    f"Failed to import object {data.get('id', '?')}: {exc}"
                )
                result.skipped_count += 1

        return result

    def _import_object(
        self,
        exported: ExportedObject,
        *,
        principal_id: str,
        recompute_checksums: bool,
    ) -> str:
        """Import a single object with all its versions. Returns new object ID."""
        new_id = generate_id()
        ts = now_utc()
        num_versions = len(exported.versions)

        # Create the scoped object
        obj_stmt = sa.insert(scoped_objects).values(
            id=new_id,
            object_type=exported.object_type,
            owner_id=principal_id,
            current_version=num_versions,
            created_at=ts.isoformat(),
            lifecycle=Lifecycle.ACTIVE.name,
        )
        sql, params = compile_for(obj_stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        # Create all versions
        for ev in exported.versions:
            version_id = generate_id()

            checksum = ev.checksum
            if recompute_checksums:
                checksum = compute_checksum(ev.data)

            ver_stmt = sa.insert(object_versions).values(
                id=version_id,
                object_id=new_id,
                version=ev.version,
                data_json=json.dumps(ev.data),
                created_at=ts.isoformat(),
                created_by=principal_id,
                change_reason=ev.change_reason or "imported",
                checksum=checksum,
            )
            sql, params = compile_for(ver_stmt, self._backend.dialect)
            self._backend.execute(sql, params)

        return new_id
