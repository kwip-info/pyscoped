"""Tests for Glacial Archival (A8 — archival module)."""

from __future__ import annotations

import json

import pytest

from scoped.storage.archival import (
    ArchiveEntry,
    ArchiveManager,
    GlacialArchive,
    archive_from_row,
)
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
    backend, owner_id: str, object_type: str = "document", num_versions: int = 1,
    data: dict | None = None,
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
        backend.execute(
            "INSERT INTO object_versions (id, object_id, version, data_json, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (vid, oid, v, json.dumps(version_data), ts, owner_id),
        )
    return oid


# ===========================================================================
# GlacialArchive model
# ===========================================================================

class TestGlacialArchiveModel:
    def test_archive_from_row(self):
        ts = now_utc()
        row = {
            "id": "arc1", "name": "test archive", "description": "desc",
            "object_ids_json": '["obj1", "obj2"]',
            "owner_id": "user1", "created_at": ts.isoformat(),
            "sealed": 0, "sealed_at": None,
            "content_hash": "abc123", "compressed_size": 100,
            "original_size": 500, "entry_count": 3,
            "lifecycle": "ACTIVE",
        }
        a = archive_from_row(row)
        assert a.id == "arc1"
        assert a.object_ids == ["obj1", "obj2"]
        assert a.sealed is False
        assert a.sealed_at is None
        assert a.entry_count == 3

    def test_sealed_archive_from_row(self):
        ts = now_utc()
        row = {
            "id": "arc1", "name": "sealed", "description": "",
            "object_ids_json": '["obj1"]',
            "owner_id": "user1", "created_at": ts.isoformat(),
            "sealed": 1, "sealed_at": ts.isoformat(),
            "content_hash": "def456", "compressed_size": 50,
            "original_size": 200, "entry_count": 1,
            "lifecycle": "ACTIVE",
        }
        a = archive_from_row(row)
        assert a.sealed is True
        assert a.sealed_at is not None


# ===========================================================================
# ArchiveManager — create
# ===========================================================================

class TestArchiveCreate:
    def test_create_archive(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner, num_versions=2)
        mgr = ArchiveManager(sqlite_backend)

        archive = mgr.create_archive(
            object_ids=[oid], owner_id=owner, name="Test Archive",
        )

        assert archive.name == "Test Archive"
        assert archive.object_ids == [oid]
        assert archive.sealed is False
        assert archive.entry_count == 2  # 2 versions
        assert archive.compressed_size > 0
        assert archive.original_size > 0
        assert archive.compressed_size <= archive.original_size
        assert len(archive.content_hash) == 64  # SHA-256 hex

    def test_create_archive_multiple_objects(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid1 = _create_object_with_versions(sqlite_backend, owner, num_versions=1)
        oid2 = _create_object_with_versions(sqlite_backend, owner, num_versions=3)
        mgr = ArchiveManager(sqlite_backend)

        archive = mgr.create_archive(
            object_ids=[oid1, oid2], owner_id=owner,
        )

        assert archive.entry_count == 4  # 1 + 3

    def test_create_archive_empty_ids_raises(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        mgr = ArchiveManager(sqlite_backend)

        with pytest.raises(ValueError, match="no object IDs"):
            mgr.create_archive(object_ids=[], owner_id=owner)

    def test_create_archive_default_name(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner)
        mgr = ArchiveManager(sqlite_backend)

        archive = mgr.create_archive(object_ids=[oid], owner_id=owner)
        assert archive.name.startswith("archive-")


# ===========================================================================
# ArchiveManager — seal
# ===========================================================================

class TestArchiveSeal:
    def test_seal_archive(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner)
        mgr = ArchiveManager(sqlite_backend)

        archive = mgr.create_archive(object_ids=[oid], owner_id=owner)
        sealed = mgr.seal_archive(archive.id)

        assert sealed.sealed is True
        assert sealed.sealed_at is not None

    def test_seal_already_sealed_raises(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner)
        mgr = ArchiveManager(sqlite_backend)

        archive = mgr.create_archive(object_ids=[oid], owner_id=owner)
        mgr.seal_archive(archive.id)

        with pytest.raises(ValueError, match="already sealed"):
            mgr.seal_archive(archive.id)

    def test_seal_nonexistent_raises(self, sqlite_backend):
        mgr = ArchiveManager(sqlite_backend)
        with pytest.raises(ValueError, match="not found"):
            mgr.seal_archive("nonexistent")


# ===========================================================================
# ArchiveManager — read / list
# ===========================================================================

class TestArchiveRead:
    def test_get_archive(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner)
        mgr = ArchiveManager(sqlite_backend)

        archive = mgr.create_archive(object_ids=[oid], owner_id=owner, name="My Archive")
        fetched = mgr.get_archive(archive.id)

        assert fetched is not None
        assert fetched.name == "My Archive"

    def test_get_archive_not_found(self, sqlite_backend):
        mgr = ArchiveManager(sqlite_backend)
        assert mgr.get_archive("nonexistent") is None

    def test_list_archives(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner)
        mgr = ArchiveManager(sqlite_backend)

        mgr.create_archive(object_ids=[oid], owner_id=owner, name="A1")
        mgr.create_archive(object_ids=[oid], owner_id=owner, name="A2")

        archives = mgr.list_archives()
        assert len(archives) == 2

    def test_list_archives_by_owner(self, sqlite_backend):
        owner1 = _setup_principal(sqlite_backend)
        owner2 = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner1)
        mgr = ArchiveManager(sqlite_backend)

        mgr.create_archive(object_ids=[oid], owner_id=owner1)
        mgr.create_archive(object_ids=[oid], owner_id=owner2)

        archives = mgr.list_archives(owner_id=owner1)
        assert len(archives) == 1
        assert archives[0].owner_id == owner1

    def test_list_sealed_only(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner)
        mgr = ArchiveManager(sqlite_backend)

        a1 = mgr.create_archive(object_ids=[oid], owner_id=owner)
        mgr.create_archive(object_ids=[oid], owner_id=owner)
        mgr.seal_archive(a1.id)

        sealed = mgr.list_archives(sealed_only=True)
        assert len(sealed) == 1
        assert sealed[0].id == a1.id


# ===========================================================================
# ArchiveManager — extract
# ===========================================================================

class TestArchiveExtract:
    def test_extract_archive(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(
            sqlite_backend, owner, num_versions=2,
            data={"title": "test doc"},
        )
        mgr = ArchiveManager(sqlite_backend)
        archive = mgr.create_archive(object_ids=[oid], owner_id=owner)

        entries = mgr.extract_archive(archive.id)
        assert len(entries) == 2
        assert all(isinstance(e, ArchiveEntry) for e in entries)
        assert entries[0].object_id == oid
        assert entries[0].data == {"title": "test doc"}

    def test_extract_specific_object(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid1 = _create_object_with_versions(sqlite_backend, owner, num_versions=1)
        oid2 = _create_object_with_versions(sqlite_backend, owner, num_versions=2)
        mgr = ArchiveManager(sqlite_backend)

        archive = mgr.create_archive(object_ids=[oid1, oid2], owner_id=owner)

        entries = mgr.extract_object(archive.id, oid2)
        assert len(entries) == 2
        assert all(e.object_id == oid2 for e in entries)

    def test_extract_nonexistent_raises(self, sqlite_backend):
        mgr = ArchiveManager(sqlite_backend)
        with pytest.raises(ValueError, match="not found"):
            mgr.extract_archive("nonexistent")


# ===========================================================================
# ArchiveManager — verify
# ===========================================================================

class TestArchiveVerify:
    def test_verify_valid(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner)
        mgr = ArchiveManager(sqlite_backend)

        archive = mgr.create_archive(object_ids=[oid], owner_id=owner)
        assert mgr.verify_archive(archive.id) is True

    def test_verify_corrupted(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner)
        mgr = ArchiveManager(sqlite_backend)

        archive = mgr.create_archive(object_ids=[oid], owner_id=owner)

        # Corrupt the content hash
        sqlite_backend.execute(
            "UPDATE glacial_archives SET content_hash = 'corrupted' WHERE id = ?",
            (archive.id,),
        )

        assert mgr.verify_archive(archive.id) is False

    def test_verify_nonexistent_raises(self, sqlite_backend):
        mgr = ArchiveManager(sqlite_backend)
        with pytest.raises(ValueError, match="not found"):
            mgr.verify_archive("nonexistent")


# ===========================================================================
# ArchiveManager — delete
# ===========================================================================

class TestArchiveDelete:
    def test_delete_unsealed(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner)
        mgr = ArchiveManager(sqlite_backend)

        archive = mgr.create_archive(object_ids=[oid], owner_id=owner)
        mgr.delete_archive(archive.id)

        assert mgr.get_archive(archive.id) is None

    def test_delete_sealed_raises(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid = _create_object_with_versions(sqlite_backend, owner)
        mgr = ArchiveManager(sqlite_backend)

        archive = mgr.create_archive(object_ids=[oid], owner_id=owner)
        mgr.seal_archive(archive.id)

        with pytest.raises(ValueError, match="sealed"):
            mgr.delete_archive(archive.id)

    def test_delete_nonexistent_raises(self, sqlite_backend):
        mgr = ArchiveManager(sqlite_backend)
        with pytest.raises(ValueError, match="not found"):
            mgr.delete_archive("nonexistent")
