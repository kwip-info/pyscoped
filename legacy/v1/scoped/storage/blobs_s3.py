"""Amazon S3 blob storage backend.

Stores binary content in an S3 bucket. Requires ``boto3`` —
install via ``pip install pyscoped[aws]``.
"""

from __future__ import annotations

from typing import Any

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError as exc:
    raise ImportError(
        "S3 blob backend requires boto3. Install with: pip install pyscoped[aws]"
    ) from exc

from scoped.storage.blobs import BlobBackend


class S3BlobBackend(BlobBackend):
    """S3-backed binary content storage.

    Blobs are stored under ``prefix`` in the given bucket, sharded by
    the first four characters of the blob_id (same layout as
    ``LocalBlobBackend``).

    Args:
        bucket: S3 bucket name.
        prefix: Key prefix (default ``"blobs/"``).
        region_name: AWS region. Uses default if omitted.
        s3_client: Pre-configured ``boto3`` S3 client. If provided,
                   other credential arguments are ignored.
    """

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "blobs/",
        region_name: str | None = None,
        s3_client: Any | None = None,
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix
        if s3_client is not None:
            self._s3 = s3_client
        else:
            kwargs: dict[str, str] = {}
            if region_name:
                kwargs["region_name"] = region_name
            self._s3 = boto3.client("s3", **kwargs)

    def _key(self, blob_id: str) -> str:
        """Build a sharded S3 key for a blob."""
        return f"{self._prefix}{blob_id[:2]}/{blob_id[2:4]}/{blob_id}"

    def store(self, blob_id: str, data: bytes) -> str:
        key = self._key(blob_id)
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data)
        return key

    def retrieve(self, storage_path: str) -> bytes:
        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=storage_path)
            return response["Body"].read()
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "NoSuchKey":
                raise FileNotFoundError(f"Blob not found: {storage_path}") from exc
            raise

    def delete(self, storage_path: str) -> bool:
        if not self.exists(storage_path):
            return False
        self._s3.delete_object(Bucket=self._bucket, Key=storage_path)
        return True

    def exists(self, storage_path: str) -> bool:
        try:
            self._s3.head_object(Bucket=self._bucket, Key=storage_path)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "404":
                return False
            raise
