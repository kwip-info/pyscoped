"""Tests for GCS blob backend (mocked — no real GCP calls)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

try:
    from google.cloud import storage as gcs  # noqa: F401
    from google.api_core import exceptions as gcp_exc  # noqa: F401

    _HAS_GCS = True
except ImportError:
    _HAS_GCS = False

pytestmark = pytest.mark.skipif(not _HAS_GCS, reason="google-cloud-storage not installed")


@pytest.fixture
def mock_gcs_client():
    """A mocked GCS client with in-memory storage."""
    store: dict[str, bytes] = {}
    client = MagicMock()
    bucket = MagicMock()

    def _blob(key):
        b = MagicMock()
        b.name = key

        def _upload(data, content_type=None):
            store[key] = data if isinstance(data, bytes) else data.encode()

        def _download():
            if key not in store:
                from google.api_core.exceptions import NotFound

                raise NotFound(f"Blob not found: {key}")
            return store[key]

        def _delete():
            if key not in store:
                from google.api_core.exceptions import NotFound

                raise NotFound(f"Blob not found: {key}")
            del store[key]

        def _exists():
            return key in store

        b.upload_from_string = _upload
        b.download_as_bytes = _download
        b.delete = _delete
        b.exists = _exists
        return b

    bucket.blob = _blob
    client.bucket.return_value = bucket
    client._store = store  # for test assertions
    return client


@pytest.fixture
def gcs_backend(mock_gcs_client):
    from scoped.storage.blobs_gcs import GCSBlobBackend

    return GCSBlobBackend("test-bucket", client=mock_gcs_client)


class TestGCSBlobBackend:
    def test_store_and_retrieve(self, gcs_backend):
        data = b"hello world"
        path = gcs_backend.store("blob-123", data)
        assert isinstance(path, str)
        assert gcs_backend.retrieve(path) == data

    def test_sharded_key(self, gcs_backend):
        path = gcs_backend.store("abcdef1234", b"x")
        assert path == "blobs/ab/cd/abcdef1234"

    def test_retrieve_not_found(self, gcs_backend):
        with pytest.raises(FileNotFoundError):
            gcs_backend.retrieve("nonexistent/key")

    def test_delete(self, gcs_backend):
        path = gcs_backend.store("del-test", b"data")
        assert gcs_backend.exists(path) is True
        assert gcs_backend.delete(path) is True
        assert gcs_backend.exists(path) is False

    def test_delete_not_found(self, gcs_backend):
        assert gcs_backend.delete("nope") is False

    def test_exists(self, gcs_backend):
        assert gcs_backend.exists("nothing") is False
        path = gcs_backend.store("exists-test", b"yes")
        assert gcs_backend.exists(path) is True

    def test_binary_data(self, gcs_backend):
        data = bytes(range(256))
        path = gcs_backend.store("bin-test", data)
        assert gcs_backend.retrieve(path) == data

    def test_custom_prefix(self, mock_gcs_client):
        from scoped.storage.blobs_gcs import GCSBlobBackend

        backend = GCSBlobBackend("bucket", prefix="custom/", client=mock_gcs_client)
        path = backend.store("abcdef", b"x")
        assert path.startswith("custom/")
