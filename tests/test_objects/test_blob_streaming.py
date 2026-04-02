"""Tests for 7A: Blob streaming — store_stream/read_stream."""

import io
import os
import tempfile

import pytest

from scoped.objects.blobs import BlobManager
from scoped.storage.blobs import InMemoryBlobBackend, LocalBlobBackend
from scoped.storage.sa_sqlite import SASQLiteBackend


@pytest.fixture
def db_backend():
    b = SASQLiteBackend(":memory:")
    b.initialize()
    yield b
    b.close()


@pytest.fixture
def mem_blob():
    return InMemoryBlobBackend()


@pytest.fixture
def local_blob(tmp_path):
    return LocalBlobBackend(str(tmp_path / "blobs"))


@pytest.fixture
def manager(db_backend, mem_blob):
    from scoped.identity.principal import PrincipalStore
    ps = PrincipalStore(db_backend)
    ps.create_principal(kind="user", display_name="Alice", principal_id="alice")
    ps.create_principal(kind="user", display_name="Bob", principal_id="bob")
    return BlobManager(db_backend, mem_blob)


@pytest.fixture
def local_manager(db_backend, local_blob):
    from scoped.identity.principal import PrincipalStore
    ps = PrincipalStore(db_backend)
    ps.create_principal(kind="user", display_name="Alice", principal_id="alice")
    return BlobManager(db_backend, local_blob)


# -- Backend-level streaming tests -------------------------------------------

class TestInMemoryBlobStreaming:
    def test_store_stream(self, mem_blob):
        data = b"hello streaming world"
        fp = io.BytesIO(data)
        path = mem_blob.store_stream("blob-1", fp)
        assert mem_blob.retrieve(path) == data

    def test_retrieve_stream(self, mem_blob):
        data = b"chunk test data"
        path = mem_blob.store("blob-2", data)
        chunks = list(mem_blob.retrieve_stream(path))
        assert b"".join(chunks) == data

    def test_retrieve_stream_not_found(self, mem_blob):
        with pytest.raises(FileNotFoundError):
            list(mem_blob.retrieve_stream("mem://nonexistent"))


class TestLocalBlobStreaming:
    def test_store_stream(self, local_blob):
        data = b"local streaming content" * 1000
        fp = io.BytesIO(data)
        path = local_blob.store_stream("blob-1", fp)
        assert local_blob.retrieve(path) == data

    def test_retrieve_stream_chunks(self, local_blob):
        # Create data larger than 64KB to test chunking
        data = os.urandom(200_000)
        path = local_blob.store("blob-big", data)
        chunks = list(local_blob.retrieve_stream(path))
        reassembled = b"".join(chunks)
        assert reassembled == data
        # Should produce multiple chunks
        assert len(chunks) > 1

    def test_retrieve_stream_not_found(self, local_blob):
        with pytest.raises(FileNotFoundError):
            list(local_blob.retrieve_stream("no/such/path"))

    def test_store_stream_chunked_write(self, local_blob):
        """Verify store_stream writes in chunks (not all at once)."""
        data = os.urandom(150_000)
        fp = io.BytesIO(data)
        path = local_blob.store_stream("blob-chunked", fp)
        assert local_blob.retrieve(path) == data


# -- Manager-level streaming tests -------------------------------------------

class TestBlobManagerStoreStream:
    def test_store_stream_basic(self, manager):
        data = b"streamed blob content"
        fp = io.BytesIO(data)

        ref = manager.store_stream(
            fp=fp,
            filename="test.bin",
            content_type="application/octet-stream",
            owner_id="alice",
        )

        assert ref.filename == "test.bin"
        assert ref.size_bytes == len(data)
        assert ref.owner_id == "alice"
        assert ref.current_version == 1

    def test_store_stream_hash_matches_non_streamed(self, manager):
        """Streamed and non-streamed store produce identical content hashes."""
        data = b"identical content for hash comparison"

        ref_normal = manager.store(
            data=data, filename="a.bin",
            content_type="application/octet-stream", owner_id="alice",
        )
        ref_stream = manager.store_stream(
            fp=io.BytesIO(data), filename="b.bin",
            content_type="application/octet-stream", owner_id="alice",
        )

        assert ref_normal.content_hash == ref_stream.content_hash
        assert ref_normal.size_bytes == ref_stream.size_bytes

    def test_store_stream_isolation(self, manager):
        """Streamed blob is only visible to the owner."""
        data = b"secret stream"
        ref = manager.store_stream(
            fp=io.BytesIO(data),
            filename="secret.bin",
            content_type="application/octet-stream",
            owner_id="alice",
        )

        # Owner can read
        assert manager.get(ref.id, principal_id="alice") is not None
        # Non-owner cannot
        assert manager.get(ref.id, principal_id="bob") is None


class TestBlobManagerReadStream:
    def test_read_stream_basic(self, manager):
        data = b"read me in chunks"
        ref = manager.store(
            data=data, filename="read.bin",
            content_type="application/octet-stream", owner_id="alice",
        )

        chunks = list(manager.read_stream(ref.id, principal_id="alice"))
        assert b"".join(chunks) == data

    def test_read_stream_isolation_enforced(self, manager):
        data = b"owner only"
        ref = manager.store(
            data=data, filename="private.bin",
            content_type="application/octet-stream", owner_id="alice",
        )

        from scoped.exceptions import AccessDeniedError
        with pytest.raises(AccessDeniedError):
            list(manager.read_stream(ref.id, principal_id="bob"))

    def test_large_blob_round_trip(self, local_manager):
        """Large blob stored via stream, read back via stream, matches."""
        data = os.urandom(300_000)

        ref = local_manager.store_stream(
            fp=io.BytesIO(data),
            filename="large.bin",
            content_type="application/octet-stream",
            owner_id="alice",
        )

        chunks = list(local_manager.read_stream(ref.id, principal_id="alice"))
        reassembled = b"".join(chunks)
        assert reassembled == data
        assert ref.content_hash == InMemoryBlobBackend.compute_content_hash(data)
