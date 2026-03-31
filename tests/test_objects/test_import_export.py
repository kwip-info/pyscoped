"""Tests for Data Import / Export (A9)."""

from __future__ import annotations

import json

import pytest

from scoped.objects.export import (
    ExportManifest,
    ExportPackage,
    ExportedObject,
    ExportedVersion,
    Exporter,
    FORMAT_VERSION,
)
from scoped.objects.import_ import ImportResult, Importer
from scoped.objects.models import compute_checksum
from scoped.types import Lifecycle, generate_id, now_utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_principal(backend) -> str:
    pid = generate_id()
    ts = now_utc().isoformat()
    backend.execute(
        "INSERT OR IGNORE INTO registry_entries "
        "(id, urn, kind, namespace, name, registered_at, registered_by) "
        "VALUES ('reg_stub', 'scoped:MODEL:test:stub:1', 'MODEL', 'test', 'stub', ?, 'system')",
        (ts,),
    )
    backend.execute(
        "INSERT INTO principals (id, kind, display_name, registry_entry_id, created_at) "
        "VALUES (?, 'user', 'Test User', 'reg_stub', ?)",
        (pid, ts),
    )
    return pid


def _create_object_with_versions(
    backend, owner_id: str, object_type: str = "document",
    num_versions: int = 1, data: dict | None = None,
) -> str:
    """Create a scoped object with N versions. Returns object_id."""
    oid = generate_id()
    ts = now_utc().isoformat()
    backend.execute(
        "INSERT INTO scoped_objects (id, object_type, owner_id, current_version, created_at, lifecycle) "
        "VALUES (?, ?, ?, ?, ?, 'ACTIVE')",
        (oid, object_type, owner_id, num_versions, ts),
    )
    for v in range(1, num_versions + 1):
        vid = generate_id()
        version_data = data or {"version": v, "content": f"data for v{v}"}
        checksum = compute_checksum(version_data)
        backend.execute(
            "INSERT INTO object_versions "
            "(id, object_id, version, data_json, created_at, created_by, change_reason, checksum) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (vid, oid, v, json.dumps(version_data), ts, owner_id, f"created v{v}", checksum),
        )
    return oid


# ===========================================================================
# ExportPackage serialization
# ===========================================================================

class TestExportPackageSerialization:
    def _sample_package(self) -> ExportPackage:
        return ExportPackage(
            manifest=ExportManifest(
                format_version="1.0",
                exported_at="2026-01-01T00:00:00+00:00",
                exported_by="user1",
                object_count=1,
                version_count=2,
            ),
            objects=[
                ExportedObject(
                    id="obj1",
                    object_type="document",
                    owner_id="user1",
                    created_at="2026-01-01T00:00:00+00:00",
                    lifecycle="ACTIVE",
                    versions=[
                        ExportedVersion(
                            version=1,
                            data={"title": "hello"},
                            created_at="2026-01-01T00:00:00+00:00",
                            created_by="user1",
                            change_reason="created",
                            checksum="abc123",
                        ),
                        ExportedVersion(
                            version=2,
                            data={"title": "updated"},
                            created_at="2026-01-01T01:00:00+00:00",
                            created_by="user1",
                            change_reason="edited",
                            checksum="def456",
                        ),
                    ],
                )
            ],
        )

    def test_to_dict(self):
        pkg = self._sample_package()
        d = pkg.to_dict()
        assert d["manifest"]["format_version"] == "1.0"
        assert d["manifest"]["object_count"] == 1
        assert len(d["objects"]) == 1
        assert len(d["objects"][0]["versions"]) == 2

    def test_from_dict_round_trip(self):
        pkg = self._sample_package()
        d = pkg.to_dict()
        restored = ExportPackage.from_dict(d)
        assert restored.manifest.format_version == pkg.manifest.format_version
        assert restored.manifest.object_count == pkg.manifest.object_count
        assert len(restored.objects) == 1
        assert restored.objects[0].id == "obj1"
        assert restored.objects[0].versions[1].data == {"title": "updated"}

    def test_to_json(self):
        pkg = self._sample_package()
        raw = pkg.to_json()
        parsed = json.loads(raw)
        assert parsed["manifest"]["format_version"] == "1.0"

    def test_from_json_round_trip(self):
        pkg = self._sample_package()
        raw = pkg.to_json()
        restored = ExportPackage.from_json(raw)
        assert restored.manifest.exported_by == "user1"
        assert restored.objects[0].versions[0].checksum == "abc123"

    def test_to_json_with_indent(self):
        pkg = self._sample_package()
        raw = pkg.to_json(indent=2)
        assert "\n" in raw  # pretty-printed


# ===========================================================================
# Exporter
# ===========================================================================

class TestExporterSingle:
    def test_export_single_object(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner, num_versions=2)
        exporter = Exporter(sqlite_backend)

        pkg = exporter.export_object(oid, principal_id=owner)

        assert pkg.manifest.format_version == FORMAT_VERSION
        assert pkg.manifest.exported_by == owner
        assert pkg.manifest.object_count == 1
        assert pkg.manifest.version_count == 2
        assert len(pkg.objects) == 1
        assert pkg.objects[0].id == oid
        assert len(pkg.objects[0].versions) == 2

    def test_export_nonexistent_object(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        exporter = Exporter(sqlite_backend)

        pkg = exporter.export_object("nonexistent", principal_id=owner)

        assert pkg.manifest.object_count == 0
        assert len(pkg.objects) == 0


class TestExporterMultiple:
    def test_export_multiple_objects(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid1 = _create_object_with_versions(sqlite_backend, owner, num_versions=1)
        oid2 = _create_object_with_versions(sqlite_backend, owner, num_versions=3)
        exporter = Exporter(sqlite_backend)

        pkg = exporter.export_objects([oid1, oid2], principal_id=owner)

        assert pkg.manifest.object_count == 2
        assert pkg.manifest.version_count == 4
        ids = {o.id for o in pkg.objects}
        assert ids == {oid1, oid2}

    def test_export_skips_non_owned(self, sqlite_backend):
        owner1 = _setup_principal(sqlite_backend)
        owner2 = _setup_principal(sqlite_backend)
        oid1 = _create_object_with_versions(sqlite_backend, owner1)
        oid2 = _create_object_with_versions(sqlite_backend, owner2)
        exporter = Exporter(sqlite_backend)

        pkg = exporter.export_objects([oid1, oid2], principal_id=owner1)

        assert pkg.manifest.object_count == 1
        assert pkg.objects[0].id == oid1

    def test_export_preserves_version_data(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(
            sqlite_backend, owner, data={"key": "value"},
        )
        exporter = Exporter(sqlite_backend)

        pkg = exporter.export_object(oid, principal_id=owner)

        assert pkg.objects[0].versions[0].data == {"key": "value"}

    def test_export_preserves_checksums(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        data = {"field": "test"}
        oid = _create_object_with_versions(sqlite_backend, owner, data=data)
        exporter = Exporter(sqlite_backend)

        pkg = exporter.export_object(oid, principal_id=owner)

        expected_checksum = compute_checksum(data)
        assert pkg.objects[0].versions[0].checksum == expected_checksum


class TestExporterByType:
    def test_export_by_type(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        _create_object_with_versions(sqlite_backend, owner, object_type="document")
        _create_object_with_versions(sqlite_backend, owner, object_type="document")
        _create_object_with_versions(sqlite_backend, owner, object_type="note")
        exporter = Exporter(sqlite_backend)

        pkg = exporter.export_by_type("document", principal_id=owner)

        assert pkg.manifest.object_count == 2
        assert all(o.object_type == "document" for o in pkg.objects)

    def test_export_by_type_no_matches(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        exporter = Exporter(sqlite_backend)

        pkg = exporter.export_by_type("nonexistent", principal_id=owner)

        assert pkg.manifest.object_count == 0


# ===========================================================================
# Importer
# ===========================================================================

class TestImporterBasic:
    def test_import_package(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner, num_versions=2)
        exporter = Exporter(sqlite_backend)
        pkg = exporter.export_object(oid, principal_id=owner)

        importer_user = _setup_principal(sqlite_backend)
        imp = Importer(sqlite_backend)
        result = imp.import_package(pkg, principal_id=importer_user)

        assert result.imported_count == 1
        assert result.version_count == 2
        assert result.skipped_count == 0
        assert len(result.errors) == 0
        assert oid in result.id_mapping
        assert result.id_mapping[oid] != oid  # new ID

    def test_import_creates_new_objects(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner)
        exporter = Exporter(sqlite_backend)
        pkg = exporter.export_object(oid, principal_id=owner)

        importer_user = _setup_principal(sqlite_backend)
        imp = Importer(sqlite_backend)
        result = imp.import_package(pkg, principal_id=importer_user)

        new_id = result.id_mapping[oid]
        row = sqlite_backend.fetch_one(
            "SELECT * FROM scoped_objects WHERE id = ?", (new_id,),
        )
        assert row is not None
        assert row["owner_id"] == importer_user
        assert row["object_type"] == "document"

    def test_import_preserves_version_data(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        data = {"title": "my doc", "body": "content"}
        oid = _create_object_with_versions(sqlite_backend, owner, data=data)
        exporter = Exporter(sqlite_backend)
        pkg = exporter.export_object(oid, principal_id=owner)

        importer_user = _setup_principal(sqlite_backend)
        imp = Importer(sqlite_backend)
        result = imp.import_package(pkg, principal_id=importer_user)

        new_id = result.id_mapping[oid]
        version_row = sqlite_backend.fetch_one(
            "SELECT * FROM object_versions WHERE object_id = ?", (new_id,),
        )
        assert json.loads(version_row["data_json"]) == data

    def test_import_recomputes_checksums(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        data = {"key": "value"}
        oid = _create_object_with_versions(sqlite_backend, owner, data=data)
        exporter = Exporter(sqlite_backend)
        pkg = exporter.export_object(oid, principal_id=owner)

        importer_user = _setup_principal(sqlite_backend)
        imp = Importer(sqlite_backend)
        result = imp.import_package(pkg, principal_id=importer_user)

        new_id = result.id_mapping[oid]
        version_row = sqlite_backend.fetch_one(
            "SELECT * FROM object_versions WHERE object_id = ?", (new_id,),
        )
        assert version_row["checksum"] == compute_checksum(data)


class TestImporterFiltering:
    def test_type_filter(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid1 = _create_object_with_versions(sqlite_backend, owner, object_type="document")
        oid2 = _create_object_with_versions(sqlite_backend, owner, object_type="note")
        exporter = Exporter(sqlite_backend)
        pkg = exporter.export_objects([oid1, oid2], principal_id=owner)

        importer_user = _setup_principal(sqlite_backend)
        imp = Importer(sqlite_backend)
        result = imp.import_package(
            pkg, principal_id=importer_user, object_type_filter="document",
        )

        assert result.imported_count == 1
        assert result.skipped_count == 1
        assert oid1 in result.id_mapping
        assert oid2 not in result.id_mapping

    def test_skip_checksums(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner)
        exporter = Exporter(sqlite_backend)
        pkg = exporter.export_object(oid, principal_id=owner)

        importer_user = _setup_principal(sqlite_backend)
        imp = Importer(sqlite_backend)
        result = imp.import_package(
            pkg, principal_id=importer_user, recompute_checksums=False,
        )

        new_id = result.id_mapping[oid]
        version_row = sqlite_backend.fetch_one(
            "SELECT * FROM object_versions WHERE object_id = ?", (new_id,),
        )
        # Should use original checksum from export
        assert version_row["checksum"] is not None


class TestImporterConvenience:
    def test_import_from_dict(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner)
        exporter = Exporter(sqlite_backend)
        pkg = exporter.export_object(oid, principal_id=owner)

        importer_user = _setup_principal(sqlite_backend)
        imp = Importer(sqlite_backend)
        result = imp.import_from_dict(pkg.to_dict(), principal_id=importer_user)

        assert result.imported_count == 1

    def test_import_from_json(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner)
        exporter = Exporter(sqlite_backend)
        pkg = exporter.export_object(oid, principal_id=owner)

        importer_user = _setup_principal(sqlite_backend)
        imp = Importer(sqlite_backend)
        result = imp.import_from_json(pkg.to_json(), principal_id=importer_user)

        assert result.imported_count == 1


class TestImporterErrors:
    def test_empty_package(self, sqlite_backend):
        pkg = ExportPackage(
            manifest=ExportManifest(
                format_version="1.0",
                exported_at="2026-01-01T00:00:00",
                exported_by="user1",
                object_count=0,
                version_count=0,
            ),
            objects=[],
        )
        importer_user = _setup_principal(sqlite_backend)
        imp = Importer(sqlite_backend)
        result = imp.import_package(pkg, principal_id=importer_user)

        assert result.imported_count == 0
        assert result.skipped_count == 0


# ===========================================================================
# Round-trip: export → import
# ===========================================================================

class TestRoundTrip:
    def test_export_import_round_trip(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        data = {"title": "round trip", "tags": ["a", "b"]}
        oid = _create_object_with_versions(
            sqlite_backend, owner, num_versions=3, data=data,
        )
        exporter = Exporter(sqlite_backend)
        pkg = exporter.export_object(oid, principal_id=owner)

        importer_user = _setup_principal(sqlite_backend)
        imp = Importer(sqlite_backend)
        result = imp.import_package(pkg, principal_id=importer_user)

        new_id = result.id_mapping[oid]
        versions = sqlite_backend.fetch_all(
            "SELECT * FROM object_versions WHERE object_id = ? ORDER BY version",
            (new_id,),
        )
        assert len(versions) == 3
        for v in versions:
            assert json.loads(v["data_json"]) == data

    def test_round_trip_via_json(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner, num_versions=2)
        exporter = Exporter(sqlite_backend)
        pkg = exporter.export_object(oid, principal_id=owner)

        json_str = pkg.to_json()

        importer_user = _setup_principal(sqlite_backend)
        imp = Importer(sqlite_backend)
        result = imp.import_from_json(json_str, principal_id=importer_user)

        assert result.imported_count == 1
        assert result.version_count == 2

    def test_round_trip_multiple_objects(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid1 = _create_object_with_versions(sqlite_backend, owner, object_type="doc", num_versions=1)
        oid2 = _create_object_with_versions(sqlite_backend, owner, object_type="note", num_versions=2)
        exporter = Exporter(sqlite_backend)
        pkg = exporter.export_objects([oid1, oid2], principal_id=owner)

        importer_user = _setup_principal(sqlite_backend)
        imp = Importer(sqlite_backend)
        result = imp.import_package(pkg, principal_id=importer_user)

        assert result.imported_count == 2
        assert result.version_count == 3
        assert len(result.id_mapping) == 2

    def test_reimport_creates_separate_objects(self, sqlite_backend):
        """Importing the same package twice creates distinct objects."""
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner)
        exporter = Exporter(sqlite_backend)
        pkg = exporter.export_object(oid, principal_id=owner)

        importer_user = _setup_principal(sqlite_backend)
        imp = Importer(sqlite_backend)
        result1 = imp.import_package(pkg, principal_id=importer_user)
        result2 = imp.import_package(pkg, principal_id=importer_user)

        # Two different new IDs
        assert result1.id_mapping[oid] != result2.id_mapping[oid]
