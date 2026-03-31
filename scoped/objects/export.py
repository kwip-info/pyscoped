"""Export objects with their versions to a portable format.

Exports respect isolation: only objects owned by (or visible to) the
exporting principal are included. The export format is a self-contained
JSON-serializable package that can be imported into another Scoped instance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from scoped.exceptions import AccessDeniedError
from scoped.storage.interface import StorageBackend
from scoped.types import now_utc


# ---------------------------------------------------------------------------
# Export data models
# ---------------------------------------------------------------------------

FORMAT_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class ExportedVersion:
    """A single object version in the export package."""

    version: int
    data: dict[str, Any]
    created_at: str
    created_by: str
    change_reason: str
    checksum: str


@dataclass(frozen=True, slots=True)
class ExportedObject:
    """A complete object with all its versions in the export package."""

    id: str
    object_type: str
    owner_id: str
    created_at: str
    lifecycle: str
    versions: list[ExportedVersion]


@dataclass(frozen=True, slots=True)
class ExportManifest:
    """Metadata about the export package."""

    format_version: str
    exported_at: str
    exported_by: str
    object_count: int
    version_count: int


@dataclass(frozen=True, slots=True)
class ExportPackage:
    """A portable, self-contained export of objects and their versions."""

    manifest: ExportManifest
    objects: list[ExportedObject]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "manifest": {
                "format_version": self.manifest.format_version,
                "exported_at": self.manifest.exported_at,
                "exported_by": self.manifest.exported_by,
                "object_count": self.manifest.object_count,
                "version_count": self.manifest.version_count,
            },
            "objects": [
                {
                    "id": obj.id,
                    "object_type": obj.object_type,
                    "owner_id": obj.owner_id,
                    "created_at": obj.created_at,
                    "lifecycle": obj.lifecycle,
                    "versions": [
                        {
                            "version": v.version,
                            "data": v.data,
                            "created_at": v.created_at,
                            "created_by": v.created_by,
                            "change_reason": v.change_reason,
                            "checksum": v.checksum,
                        }
                        for v in obj.versions
                    ],
                }
                for obj in self.objects
            ],
        }

    def to_json(self, **kwargs: Any) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExportPackage:
        """Deserialize from a dict."""
        m = data["manifest"]
        manifest = ExportManifest(
            format_version=m["format_version"],
            exported_at=m["exported_at"],
            exported_by=m["exported_by"],
            object_count=m["object_count"],
            version_count=m["version_count"],
        )
        objects = [
            ExportedObject(
                id=obj["id"],
                object_type=obj["object_type"],
                owner_id=obj["owner_id"],
                created_at=obj["created_at"],
                lifecycle=obj["lifecycle"],
                versions=[
                    ExportedVersion(
                        version=v["version"],
                        data=v["data"],
                        created_at=v["created_at"],
                        created_by=v["created_by"],
                        change_reason=v["change_reason"],
                        checksum=v["checksum"],
                    )
                    for v in obj["versions"]
                ],
            )
            for obj in data["objects"]
        ]
        return cls(manifest=manifest, objects=objects)

    @classmethod
    def from_json(cls, raw: str) -> ExportPackage:
        """Deserialize from a JSON string."""
        return cls.from_dict(json.loads(raw))


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------

class Exporter:
    """Exports scoped objects to portable ExportPackage format.

    Only objects owned by the principal can be exported (Layer 3 isolation).
    For scope-aware export, pre-filter object_ids using VisibilityEngine.
    """

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def export_object(
        self,
        object_id: str,
        *,
        principal_id: str,
    ) -> ExportPackage:
        """Export a single object with all its versions."""
        return self.export_objects([object_id], principal_id=principal_id)

    def export_objects(
        self,
        object_ids: list[str],
        *,
        principal_id: str,
    ) -> ExportPackage:
        """Export multiple objects with all their versions.

        Only includes objects owned by principal_id. Objects not found
        or not owned are silently skipped.
        """
        exported: list[ExportedObject] = []
        total_versions = 0

        for oid in object_ids:
            obj_row = self._backend.fetch_one(
                "SELECT * FROM scoped_objects WHERE id = ? AND owner_id = ?",
                (oid, principal_id),
            )
            if obj_row is None:
                continue

            version_rows = self._backend.fetch_all(
                "SELECT * FROM object_versions WHERE object_id = ? ORDER BY version",
                (oid,),
            )

            versions = [
                ExportedVersion(
                    version=vr["version"],
                    data=json.loads(vr["data_json"]),
                    created_at=vr["created_at"],
                    created_by=vr["created_by"],
                    change_reason=vr["change_reason"],
                    checksum=vr["checksum"],
                )
                for vr in version_rows
            ]

            exported.append(ExportedObject(
                id=obj_row["id"],
                object_type=obj_row["object_type"],
                owner_id=obj_row["owner_id"],
                created_at=obj_row["created_at"],
                lifecycle=obj_row["lifecycle"],
                versions=versions,
            ))
            total_versions += len(versions)

        ts = now_utc()
        manifest = ExportManifest(
            format_version=FORMAT_VERSION,
            exported_at=ts.isoformat(),
            exported_by=principal_id,
            object_count=len(exported),
            version_count=total_versions,
        )

        return ExportPackage(manifest=manifest, objects=exported)

    def export_by_type(
        self,
        object_type: str,
        *,
        principal_id: str,
        limit: int = 1000,
    ) -> ExportPackage:
        """Export all objects of a given type owned by the principal."""
        rows = self._backend.fetch_all(
            "SELECT id FROM scoped_objects WHERE object_type = ? AND owner_id = ? LIMIT ?",
            (object_type, principal_id, limit),
        )
        object_ids = [r["id"] for r in rows]
        return self.export_objects(object_ids, principal_id=principal_id)
