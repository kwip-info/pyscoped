"""Tests for S3 blob backend (mocked — no real AWS calls)."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock

import pytest


def _make_client_error(code: str):
    """Build a mock botocore ClientError."""
    from botocore.exceptions import ClientError

    return ClientError(
        {"Error": {"Code": code, "Message": "test"}},
        "test_op",
    )


@pytest.fixture
def mock_s3():
    """A mocked boto3 S3 client with in-memory storage."""
    client = MagicMock()
    store: dict[str, bytes] = {}

    def _put_object(**kwargs):
        store[kwargs["Key"]] = kwargs["Body"]

    def _get_object(**kwargs):
        key = kwargs["Key"]
        if key not in store:
            raise _make_client_error("NoSuchKey")
        body = MagicMock()
        body.read.return_value = store[key]
        return {"Body": body}

    def _head_object(**kwargs):
        if kwargs["Key"] not in store:
            raise _make_client_error("404")

    def _delete_object(**kwargs):
        store.pop(kwargs["Key"], None)

    client.put_object.side_effect = _put_object
    client.get_object.side_effect = _get_object
    client.head_object.side_effect = _head_object
    client.delete_object.side_effect = _delete_object
    client._store = store  # for test assertions
    return client


@pytest.fixture
def s3_backend(mock_s3):
    from scoped.storage.blobs_s3 import S3BlobBackend

    return S3BlobBackend("test-bucket", s3_client=mock_s3)


class TestS3BlobBackend:
    def test_store_and_retrieve(self, s3_backend):
        data = b"hello world"
        path = s3_backend.store("blob-123", data)
        assert isinstance(path, str)
        assert s3_backend.retrieve(path) == data

    def test_sharded_key(self, s3_backend):
        path = s3_backend.store("abcdef1234", b"x")
        assert path == "blobs/ab/cd/abcdef1234"

    def test_retrieve_not_found(self, s3_backend):
        with pytest.raises(FileNotFoundError):
            s3_backend.retrieve("nonexistent/key")

    def test_delete(self, s3_backend):
        path = s3_backend.store("del-test", b"data")
        assert s3_backend.exists(path) is True
        assert s3_backend.delete(path) is True
        assert s3_backend.exists(path) is False

    def test_delete_not_found(self, s3_backend):
        assert s3_backend.delete("nope") is False

    def test_exists(self, s3_backend):
        assert s3_backend.exists("nothing") is False
        path = s3_backend.store("exists-test", b"yes")
        assert s3_backend.exists(path) is True

    def test_binary_data(self, s3_backend):
        data = bytes(range(256))
        path = s3_backend.store("bin-test", data)
        assert s3_backend.retrieve(path) == data

    def test_custom_prefix(self, mock_s3):
        from scoped.storage.blobs_s3 import S3BlobBackend

        backend = S3BlobBackend("bucket", prefix="custom/", s3_client=mock_s3)
        path = backend.store("abcdef", b"x")
        assert path.startswith("custom/")
