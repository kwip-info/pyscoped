"""Tests for 7B: Archive streaming — NDJSON stream export/import."""

import json

import pytest

from scoped.identity.principal import PrincipalStore
from scoped.manifest._services import build_services
from scoped.objects.export import Exporter
from scoped.objects.import_ import Importer
from scoped.storage.sa_sqlite import SASQLiteBackend


@pytest.fixture
def backend():
    b = SASQLiteBackend(":memory:")
    b.initialize()
    yield b
    b.close()


@pytest.fixture
def services(backend):
    return build_services(backend)


@pytest.fixture
def alice(services):
    return services.principals.create_principal(
        kind="user", display_name="Alice", principal_id="alice",
    )


@pytest.fixture
def exporter(backend):
    return Exporter(backend)


@pytest.fixture
def importer(backend):
    return Importer(backend)


def _create_objects(services, alice, count=3):
    """Helper to create test objects."""
    ids = []
    for i in range(count):
        obj, _ = services.manager.create(
            object_type="doc", owner_id=alice.id,
            data={"title": f"Doc {i}", "index": i},
        )
        ids.append(obj.id)
    return ids


class TestStreamExport:
    def test_first_line_is_manifest(self, services, alice, exporter):
        ids = _create_objects(services, alice, count=1)
        lines = list(exporter.stream_export(ids, principal_id="alice"))

        assert len(lines) >= 2
        manifest = json.loads(lines[0])
        assert manifest["_type"] == "manifest"
        assert manifest["format_version"] == "1.0"
        assert manifest["exported_by"] == "alice"

    def test_yields_ndjson(self, services, alice, exporter):
        ids = _create_objects(services, alice, count=3)
        lines = list(exporter.stream_export(ids, principal_id="alice"))

        # 1 manifest + 3 objects
        assert len(lines) == 4

        # Each line is valid JSON
        for line in lines:
            parsed = json.loads(line)
            assert isinstance(parsed, dict)

    def test_object_lines_have_versions(self, services, alice, exporter):
        ids = _create_objects(services, alice, count=1)
        # Add a second version
        services.manager.update(
            ids[0], principal_id="alice",
            data={"title": "Updated"}, change_reason="edit",
        )

        lines = list(exporter.stream_export(ids, principal_id="alice"))
        obj = json.loads(lines[1])
        assert obj["id"] == ids[0]
        assert obj["object_type"] == "doc"
        assert len(obj["versions"]) == 2

    def test_skips_non_owned(self, services, alice, exporter):
        ids = _create_objects(services, alice, count=1)
        # Export as bob — should get manifest only
        services.principals.create_principal(
            kind="user", display_name="Bob", principal_id="bob",
        )
        lines = list(exporter.stream_export(ids, principal_id="bob"))
        assert len(lines) == 1  # manifest only


class TestStreamExportByType:
    def test_exports_matching_type(self, services, alice, exporter):
        _create_objects(services, alice, count=2)
        # Create one of different type
        services.manager.create(
            object_type="note", owner_id="alice", data={"text": "hi"},
        )

        lines = list(exporter.stream_export_by_type(
            "doc", principal_id="alice", batch_size=10,
        ))
        # 1 manifest + 2 doc objects
        assert len(lines) == 3

    def test_pagination(self, services, alice, exporter):
        _create_objects(services, alice, count=5)

        lines = list(exporter.stream_export_by_type(
            "doc", principal_id="alice", batch_size=2,
        ))
        # 1 manifest + 5 objects (fetched in batches of 2)
        assert len(lines) == 6


class TestStreamImport:
    def test_import_from_ndjson(self, services, alice, backend, exporter, importer):
        ids = _create_objects(services, alice, count=2)
        lines = list(exporter.stream_export(ids, principal_id="alice"))

        # Import as a different principal
        services.principals.create_principal(
            kind="user", display_name="Bob", principal_id="bob",
        )
        result = importer.stream_import(iter(lines), principal_id="bob")

        assert result.imported_count == 2
        assert result.version_count == 2
        assert len(result.id_mapping) == 2
        assert len(result.errors) == 0

    def test_import_skips_manifest(self, services, alice, exporter, importer):
        ids = _create_objects(services, alice, count=1)
        lines = list(exporter.stream_export(ids, principal_id="alice"))

        result = importer.stream_import(iter(lines), principal_id="alice")
        assert result.imported_count == 1

    def test_import_with_type_filter(self, services, alice, backend, exporter, importer):
        _create_objects(services, alice, count=2)
        services.manager.create(
            object_type="note", owner_id="alice", data={"text": "hi"},
        )

        # Export all
        all_ids = [
            r["id"] for r in backend.fetch_all(
                "SELECT id FROM scoped_objects WHERE owner_id = ?", ("alice",),
            )
        ]
        lines = list(exporter.stream_export(all_ids, principal_id="alice"))

        # Import only notes
        result = importer.stream_import(
            iter(lines), principal_id="alice", object_type_filter="note",
        )
        assert result.imported_count == 1
        assert result.skipped_count == 2  # 2 docs skipped

    def test_import_handles_invalid_json(self, importer):
        lines = [
            '{"_type": "manifest", "format_version": "1.0"}',
            "not valid json",
            '{"id": "x"}',  # missing required fields
        ]
        result = importer.stream_import(iter(lines), principal_id="alice")
        assert result.imported_count == 0
        assert len(result.errors) >= 2  # invalid JSON + missing fields

    def test_import_handles_empty_lines(self, importer):
        lines = ['{"_type": "manifest"}', "", "  ", '{"_type": "manifest"}']
        result = importer.stream_import(iter(lines), principal_id="alice")
        assert result.imported_count == 0
        assert result.skipped_count == 0


class TestStreamRoundTrip:
    def test_round_trip_preserves_data(self, services, alice, backend, exporter, importer):
        ids = _create_objects(services, alice, count=3)

        # Export via stream
        lines = list(exporter.stream_export(ids, principal_id="alice"))

        # Import via stream
        services.principals.create_principal(
            kind="user", display_name="Bob", principal_id="bob",
        )
        result = importer.stream_import(iter(lines), principal_id="bob")

        assert result.imported_count == 3
        assert result.version_count == 3

        # Verify data matches
        for old_id, new_id in result.id_mapping.items():
            row = backend.fetch_one(
                "SELECT data_json FROM object_versions WHERE object_id = ? AND version = 1",
                (new_id,),
            )
            assert row is not None
            data = json.loads(row["data_json"])
            assert "title" in data

    def test_stream_matches_batch_export(self, services, alice, exporter):
        """Stream export produces the same object data as batch export."""
        ids = _create_objects(services, alice, count=2)

        # Batch export
        package = exporter.export_objects(ids, principal_id="alice")
        batch_objects = {obj.id: obj for obj in package.objects}

        # Stream export
        lines = list(exporter.stream_export(ids, principal_id="alice"))
        stream_objects = {}
        for line in lines[1:]:  # skip manifest
            obj = json.loads(line)
            stream_objects[obj["id"]] = obj

        # Same objects, same data
        assert set(batch_objects.keys()) == set(stream_objects.keys())
        for oid in batch_objects:
            batch_versions = batch_objects[oid].versions
            stream_versions = stream_objects[oid]["versions"]
            assert len(batch_versions) == len(stream_versions)
            for bv, sv in zip(batch_versions, stream_versions):
                assert bv.data == sv["data"]
                assert bv.checksum == sv["checksum"]
