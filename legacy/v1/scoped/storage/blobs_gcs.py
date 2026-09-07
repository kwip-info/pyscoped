"""Google Cloud Storage blob backend.

Stores binary content in a GCS bucket. Requires ``google-cloud-storage``
— install via ``pip install pyscoped[gcp]``.
"""

from __future__ import annotations

from typing import Any

try:
    from google.cloud import storage as gcs
    from google.api_core.exceptions import NotFound
except ImportError as exc:
    raise ImportError(
        "GCS blob backend requires google-cloud-storage. "
        "Install with: pip install pyscoped[gcp]"
    ) from exc

from scoped.storage.blobs import BlobBackend


class GCSBlobBackend(BlobBackend):
    """Google Cloud Storage binary content backend.

    Blobs are stored under ``prefix`` in the given bucket, sharded by
    the first four characters of the blob_id.

    Args:
        bucket_name: GCS bucket name.
        prefix: Object name prefix (default ``"blobs/"``).
        client: Pre-configured ``google.cloud.storage.Client``. Uses
                default credentials if omitted.
    """

    def __init__(
        self,
        bucket_name: str,
        *,
        prefix: str = "blobs/",
        client: Any | None = None,
    ) -> None:
        self._client = client or gcs.Client()
        self._bucket = self._client.bucket(bucket_name)
        self._prefix = prefix

    def _key(self, blob_id: str) -> str:
        """Build a sharded GCS object name for a blob."""
        return f"{self._prefix}{blob_id[:2]}/{blob_id[2:4]}/{blob_id}"

    def store(self, blob_id: str, data: bytes) -> str:
        key = self._key(blob_id)
        blob = self._bucket.blob(key)
        blob.upload_from_string(data, content_type="application/octet-stream")
        return key

    def retrieve(self, storage_path: str) -> bytes:
        blob = self._bucket.blob(storage_path)
        try:
            return blob.download_as_bytes()
        except NotFound as exc:
            raise FileNotFoundError(f"Blob not found: {storage_path}") from exc

    def delete(self, storage_path: str) -> bool:
        blob = self._bucket.blob(storage_path)
        try:
            blob.delete()
            return True
        except NotFound:
            return False

    def exists(self, storage_path: str) -> bool:
        return self._bucket.blob(storage_path).exists()
