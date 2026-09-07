"""Tests for blob management — BlobRef, BlobVersion, BlobManager."""

import pytest

from scoped.exceptions import AccessDeniedError
from scoped.identity.principal import PrincipalStore
from scoped.objects.blobs import (
    BlobManager,
    BlobRef,
    BlobVersion,
    blob_ref_from_row,
    blob_version_from_row,
)
from scoped.storage.blobs import InMemoryBlobBackend
from scoped.types import Lifecycle


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    bob = store.create_principal(kind="user", display_name="Bob", principal_id="bob")
    return alice, bob


@pytest.fixture
def blob_backend():
    return InMemoryBlobBackend()


@pytest.fixture
def manager(sqlite_backend, blob_backend):
    return BlobManager(sqlite_backend, blob_backend)


# -----------------------------------------------------------------------
# BlobRef model
# -----------------------------------------------------------------------

class TestBlobRef:

    def test_snapshot(self, principals):
        from scoped.types import now_utc

        ref = BlobRef(
            id="b1", filename="test.txt", content_type="text/plain",
            size_bytes=100, content_hash="abc", owner_id="alice",
            created_at=now_utc(), storage_path="mem://b1",
        )
        snap = ref.snapshot()
        assert snap["id"] == "b1"
        assert snap["filename"] == "test.txt"
        assert snap["lifecycle"] == "ACTIVE"

    def test_is_active(self):
        from scoped.types import now_utc

        ref = BlobRef(
            id="b1", filename="f", content_type="t",
            size_bytes=0, content_hash="h", owner_id="o",
            created_at=now_utc(), storage_path="p",
        )
        assert ref.is_active
        ref.lifecycle = Lifecycle.ARCHIVED
        assert not ref.is_active


# -----------------------------------------------------------------------
# BlobVersion model
# -----------------------------------------------------------------------

class TestBlobVersion:

    def test_frozen(self):
        from scoped.types import now_utc

        ver = BlobVersion(
            id="v1", blob_id="b1", version=1,
            content_hash="h", size_bytes=10,
            storage_path="p", created_at=now_utc(),
            created_by="alice",
        )
        with pytest.raises(AttributeError):
            ver.version = 2

    def test_snapshot(self):
        from scoped.types import now_utc

        ver = BlobVersion(
            id="v1", blob_id="b1", version=1,
            content_hash="h", size_bytes=10,
            storage_path="p", created_at=now_utc(),
            created_by="alice", change_reason="created",
        )
        snap = ver.snapshot()
        assert snap["version"] == 1
        assert snap["change_reason"] == "created"


# -----------------------------------------------------------------------
# Row mappers
# -----------------------------------------------------------------------

class TestRowMappers:

    def test_blob_ref_from_row(self):
        row = {
            "id": "b1", "filename": "f.txt", "content_type": "text/plain",
            "size_bytes": 42, "content_hash": "abc", "owner_id": "alice",
            "created_at": "2026-01-01T00:00:00+00:00", "storage_path": "mem://b1",
            "current_version": 1, "lifecycle": "ACTIVE",
            "object_id": None, "metadata_json": "{}",
        }
        ref = blob_ref_from_row(row)
        assert ref.id == "b1"
        assert ref.filename == "f.txt"
        assert ref.size_bytes == 42

    def test_blob_version_from_row(self):
        row = {
            "id": "v1", "blob_id": "b1", "version": 1,
            "content_hash": "h", "size_bytes": 10,
            "storage_path": "p", "created_at": "2026-01-01T00:00:00+00:00",
            "created_by": "alice", "change_reason": "created",
        }
        ver = blob_version_from_row(row)
        assert ver.blob_id == "b1"
        assert ver.version == 1


# -----------------------------------------------------------------------
# BlobManager — store
# -----------------------------------------------------------------------

class TestBlobManagerStore:

    def test_store_returns_ref(self, manager, principals):
        alice, _ = principals
        ref = manager.store(
            data=b"hello world",
            filename="hello.txt",
            content_type="text/plain",
            owner_id=alice.id,
        )
        assert ref.filename == "hello.txt"
        assert ref.content_type == "text/plain"
        assert ref.size_bytes == 11
        assert ref.current_version == 1
        assert ref.is_active

    def test_store_computes_hash(self, manager, principals):
        alice, _ = principals
        ref = manager.store(
            data=b"test data",
            filename="test.bin",
            content_type="application/octet-stream",
            owner_id=alice.id,
        )
        from scoped.storage.blobs import BlobBackend

        expected = BlobBackend.compute_content_hash(b"test data")
        assert ref.content_hash == expected

    def test_store_with_object_id(self, manager, principals, sqlite_backend):
        alice, _ = principals
        # Create a scoped object to link to
        from scoped.objects.manager import ScopedManager

        obj_mgr = ScopedManager(sqlite_backend)
        obj, _ = obj_mgr.create(
            object_type="Document",
            owner_id=alice.id,
            data={"title": "doc"},
        )

        ref = manager.store(
            data=b"attachment",
            filename="file.pdf",
            content_type="application/pdf",
            owner_id=alice.id,
            object_id=obj.id,
        )
        assert ref.object_id == obj.id

    def test_store_with_metadata(self, manager, principals):
        alice, _ = principals
        ref = manager.store(
            data=b"img",
            filename="photo.jpg",
            content_type="image/jpeg",
            owner_id=alice.id,
            metadata={"width": 1920, "height": 1080},
        )
        assert ref.metadata == {"width": 1920, "height": 1080}

    def test_store_persists(self, manager, principals):
        alice, _ = principals
        ref = manager.store(
            data=b"persist me",
            filename="f.bin",
            content_type="application/octet-stream",
            owner_id=alice.id,
        )
        loaded = manager.get(ref.id, principal_id=alice.id)
        assert loaded is not None
        assert loaded.filename == "f.bin"

    def test_store_creates_version(self, manager, principals):
        alice, _ = principals
        ref = manager.store(
            data=b"v1",
            filename="f.bin",
            content_type="application/octet-stream",
            owner_id=alice.id,
        )
        ver = manager.get_version(ref.id, 1)
        assert ver is not None
        assert ver.version == 1


# -----------------------------------------------------------------------
# BlobManager — read / get
# -----------------------------------------------------------------------

class TestBlobManagerRead:

    def test_get_own_blob(self, manager, principals):
        alice, _ = principals
        ref = manager.store(
            data=b"my data", filename="f", content_type="t", owner_id=alice.id,
        )
        loaded = manager.get(ref.id, principal_id=alice.id)
        assert loaded is not None
        assert loaded.id == ref.id

    def test_get_other_blob_returns_none(self, manager, principals):
        alice, bob = principals
        ref = manager.store(
            data=b"secret", filename="f", content_type="t", owner_id=alice.id,
        )
        assert manager.get(ref.id, principal_id=bob.id) is None

    def test_get_nonexistent_returns_none(self, manager, principals):
        alice, _ = principals
        assert manager.get("nope", principal_id=alice.id) is None

    def test_get_or_raise(self, manager, principals):
        alice, bob = principals
        ref = manager.store(
            data=b"data", filename="f", content_type="t", owner_id=alice.id,
        )
        assert manager.get_or_raise(ref.id, principal_id=alice.id).id == ref.id

        with pytest.raises(AccessDeniedError):
            manager.get_or_raise(ref.id, principal_id=bob.id)

    def test_get_or_raise_not_found(self, manager, principals):
        alice, _ = principals
        with pytest.raises(AccessDeniedError):
            manager.get_or_raise("nope", principal_id=alice.id)

    def test_read_content(self, manager, principals):
        alice, _ = principals
        ref = manager.store(
            data=b"hello content", filename="f", content_type="t", owner_id=alice.id,
        )
        content = manager.read(ref.id, principal_id=alice.id)
        assert content == b"hello content"

    def test_read_isolation(self, manager, principals):
        alice, bob = principals
        ref = manager.store(
            data=b"private", filename="f", content_type="t", owner_id=alice.id,
        )
        with pytest.raises(AccessDeniedError):
            manager.read(ref.id, principal_id=bob.id)


# -----------------------------------------------------------------------
# BlobManager — update
# -----------------------------------------------------------------------

class TestBlobManagerUpdate:

    def test_update_creates_new_version(self, manager, principals):
        alice, _ = principals
        ref = manager.store(
            data=b"v1", filename="f", content_type="t", owner_id=alice.id,
        )
        updated = manager.update(
            ref.id, data=b"v2", principal_id=alice.id, change_reason="replaced",
        )
        assert updated.current_version == 2
        assert updated.size_bytes == 2

    def test_update_new_content_readable(self, manager, principals):
        alice, _ = principals
        ref = manager.store(
            data=b"original", filename="f", content_type="t", owner_id=alice.id,
        )
        manager.update(ref.id, data=b"replaced", principal_id=alice.id)
        content = manager.read(ref.id, principal_id=alice.id)
        assert content == b"replaced"

    def test_update_isolation(self, manager, principals):
        alice, bob = principals
        ref = manager.store(
            data=b"data", filename="f", content_type="t", owner_id=alice.id,
        )
        with pytest.raises(AccessDeniedError):
            manager.update(ref.id, data=b"hacked", principal_id=bob.id)

    def test_update_preserves_old_version(self, manager, principals):
        alice, _ = principals
        ref = manager.store(
            data=b"v1", filename="f", content_type="t", owner_id=alice.id,
        )
        manager.update(ref.id, data=b"v2", principal_id=alice.id)

        v1 = manager.get_version(ref.id, 1)
        v2 = manager.get_version(ref.id, 2)
        assert v1 is not None
        assert v2 is not None
        assert v1.content_hash != v2.content_hash


# -----------------------------------------------------------------------
# BlobManager — delete
# -----------------------------------------------------------------------

class TestBlobManagerDelete:

    def test_delete_archives(self, manager, principals):
        alice, _ = principals
        ref = manager.store(
            data=b"doomed", filename="f", content_type="t", owner_id=alice.id,
        )
        deleted = manager.delete(ref.id, principal_id=alice.id, reason="cleanup")
        assert deleted.lifecycle == Lifecycle.ARCHIVED

    def test_deleted_not_in_list(self, manager, principals):
        alice, _ = principals
        ref = manager.store(
            data=b"data", filename="f", content_type="t", owner_id=alice.id,
        )
        manager.delete(ref.id, principal_id=alice.id)
        blobs = manager.list_blobs(principal_id=alice.id)
        assert len(blobs) == 0

    def test_delete_isolation(self, manager, principals):
        alice, bob = principals
        ref = manager.store(
            data=b"data", filename="f", content_type="t", owner_id=alice.id,
        )
        with pytest.raises(AccessDeniedError):
            manager.delete(ref.id, principal_id=bob.id)


# -----------------------------------------------------------------------
# BlobManager — list
# -----------------------------------------------------------------------

class TestBlobManagerList:

    def test_list_own_blobs(self, manager, principals):
        alice, bob = principals
        manager.store(data=b"a1", filename="a", content_type="t", owner_id=alice.id)
        manager.store(data=b"a2", filename="b", content_type="t", owner_id=alice.id)
        manager.store(data=b"b1", filename="c", content_type="t", owner_id=bob.id)

        alice_blobs = manager.list_blobs(principal_id=alice.id)
        assert len(alice_blobs) == 2

        bob_blobs = manager.list_blobs(principal_id=bob.id)
        assert len(bob_blobs) == 1

    def test_list_by_content_type(self, manager, principals):
        alice, _ = principals
        manager.store(data=b"img", filename="a.jpg", content_type="image/jpeg", owner_id=alice.id)
        manager.store(data=b"doc", filename="b.pdf", content_type="application/pdf", owner_id=alice.id)

        images = manager.list_blobs(principal_id=alice.id, content_type="image/jpeg")
        assert len(images) == 1
        assert images[0].filename == "a.jpg"

    def test_list_by_object_id(self, manager, principals, sqlite_backend):
        alice, _ = principals
        from scoped.objects.manager import ScopedManager

        obj_mgr = ScopedManager(sqlite_backend)
        obj, _ = obj_mgr.create(object_type="Doc", owner_id=alice.id, data={})

        manager.store(data=b"a", filename="a", content_type="t", owner_id=alice.id, object_id=obj.id)
        manager.store(data=b"b", filename="b", content_type="t", owner_id=alice.id)

        linked = manager.list_blobs(principal_id=alice.id, object_id=obj.id)
        assert len(linked) == 1


# -----------------------------------------------------------------------
# BlobManager — versions
# -----------------------------------------------------------------------

class TestBlobManagerVersions:

    def test_list_versions(self, manager, principals):
        alice, _ = principals
        ref = manager.store(data=b"v1", filename="f", content_type="t", owner_id=alice.id)
        manager.update(ref.id, data=b"v2", principal_id=alice.id)
        manager.update(ref.id, data=b"v3", principal_id=alice.id)

        versions = manager.list_versions(ref.id, principal_id=alice.id)
        assert len(versions) == 3
        assert [v.version for v in versions] == [1, 2, 3]

    def test_list_versions_isolation(self, manager, principals):
        alice, bob = principals
        ref = manager.store(data=b"data", filename="f", content_type="t", owner_id=alice.id)

        with pytest.raises(AccessDeniedError):
            manager.list_versions(ref.id, principal_id=bob.id)

    def test_get_version(self, manager, principals):
        alice, _ = principals
        ref = manager.store(data=b"v1", filename="f", content_type="t", owner_id=alice.id)
        ver = manager.get_version(ref.id, 1)
        assert ver is not None
        assert ver.version == 1

    def test_get_version_nonexistent(self, manager, principals):
        alice, _ = principals
        ref = manager.store(data=b"v1", filename="f", content_type="t", owner_id=alice.id)
        assert manager.get_version(ref.id, 99) is None


# -----------------------------------------------------------------------
# BlobManager — link to object
# -----------------------------------------------------------------------

class TestBlobManagerLink:

    def test_link_to_object(self, manager, principals, sqlite_backend):
        alice, _ = principals
        from scoped.objects.manager import ScopedManager

        obj_mgr = ScopedManager(sqlite_backend)
        obj, _ = obj_mgr.create(object_type="Doc", owner_id=alice.id, data={})

        ref = manager.store(data=b"data", filename="f", content_type="t", owner_id=alice.id)
        assert ref.object_id is None

        manager.link_to_object(ref.id, obj.id)

        loaded = manager.get(ref.id, principal_id=alice.id)
        assert loaded.object_id == obj.id
