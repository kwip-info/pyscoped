"""Tests for blob storage backends."""

import tempfile

import pytest

from scoped.storage.blobs import (
    BlobBackend,
    InMemoryBlobBackend,
    LocalBlobBackend,
)


# -----------------------------------------------------------------------
# Content hash
# -----------------------------------------------------------------------

class TestContentHash:

    def test_deterministic(self):
        data = b"hello world"
        h1 = BlobBackend.compute_content_hash(data)
        h2 = BlobBackend.compute_content_hash(data)
        assert h1 == h2

    def test_different_data(self):
        h1 = BlobBackend.compute_content_hash(b"aaa")
        h2 = BlobBackend.compute_content_hash(b"bbb")
        assert h1 != h2

    def test_sha256_length(self):
        h = BlobBackend.compute_content_hash(b"test")
        assert len(h) == 64


# -----------------------------------------------------------------------
# InMemoryBlobBackend
# -----------------------------------------------------------------------

class TestInMemoryBlobBackend:

    def test_store_and_retrieve(self):
        backend = InMemoryBlobBackend()
        path = backend.store("blob-1", b"hello")
        assert backend.retrieve(path) == b"hello"

    def test_retrieve_not_found(self):
        backend = InMemoryBlobBackend()
        with pytest.raises(FileNotFoundError):
            backend.retrieve("mem://nonexistent")

    def test_delete(self):
        backend = InMemoryBlobBackend()
        path = backend.store("blob-1", b"data")
        assert backend.delete(path) is True
        assert backend.exists(path) is False

    def test_delete_not_found(self):
        backend = InMemoryBlobBackend()
        assert backend.delete("mem://nope") is False

    def test_exists(self):
        backend = InMemoryBlobBackend()
        path = backend.store("blob-1", b"data")
        assert backend.exists(path) is True
        assert backend.exists("mem://nope") is False

    def test_count(self):
        backend = InMemoryBlobBackend()
        assert backend.count == 0
        backend.store("a", b"1")
        backend.store("b", b"2")
        assert backend.count == 2

    def test_stores_binary(self):
        backend = InMemoryBlobBackend()
        data = bytes(range(256))
        path = backend.store("bin", data)
        assert backend.retrieve(path) == data

    def test_empty_bytes(self):
        backend = InMemoryBlobBackend()
        path = backend.store("empty", b"")
        assert backend.retrieve(path) == b""


# -----------------------------------------------------------------------
# LocalBlobBackend
# -----------------------------------------------------------------------

class TestLocalBlobBackend:

    def test_store_and_retrieve(self, tmp_path):
        backend = LocalBlobBackend(tmp_path)
        path = backend.store("abcdef1234567890", b"hello")
        assert backend.retrieve(path) == b"hello"

    def test_retrieve_not_found(self, tmp_path):
        backend = LocalBlobBackend(tmp_path)
        with pytest.raises(FileNotFoundError):
            backend.retrieve("no/such/file")

    def test_delete(self, tmp_path):
        backend = LocalBlobBackend(tmp_path)
        path = backend.store("abcdef1234567890", b"data")
        assert backend.delete(path) is True
        assert backend.exists(path) is False

    def test_delete_not_found(self, tmp_path):
        backend = LocalBlobBackend(tmp_path)
        assert backend.delete("no/such/file") is False

    def test_exists(self, tmp_path):
        backend = LocalBlobBackend(tmp_path)
        path = backend.store("abcdef1234567890", b"data")
        assert backend.exists(path) is True
        assert backend.exists("no/file") is False

    def test_sharding(self, tmp_path):
        backend = LocalBlobBackend(tmp_path)
        path = backend.store("abcdef1234567890", b"data")
        # Path should be: ab/cd/abcdef1234567890
        assert "ab" in path
        assert "cd" in path

    def test_creates_root_dir(self, tmp_path):
        new_root = tmp_path / "sub" / "dir"
        backend = LocalBlobBackend(new_root)
        assert new_root.exists()

    def test_binary_data(self, tmp_path):
        backend = LocalBlobBackend(tmp_path)
        data = bytes(range(256))
        path = backend.store("binary01234567890", data)
        assert backend.retrieve(path) == data
