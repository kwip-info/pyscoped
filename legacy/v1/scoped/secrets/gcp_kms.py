"""GCP Cloud KMS encryption backend for Layer 11 secrets.

Uses Google Cloud Key Management Service for server-side encryption.
Requires ``google-cloud-kms`` — install via ``pip install pyscoped[gcp]``.
"""

from __future__ import annotations

import base64
from typing import Any

try:
    from google.cloud import kms as google_kms
except ImportError as exc:
    raise ImportError(
        "GCP KMS backend requires google-cloud-kms. "
        "Install with: pip install pyscoped[gcp]"
    ) from exc

from scoped.secrets.backend import SecretBackend


class GCPKMSBackend(SecretBackend):
    """Google Cloud KMS encryption backend.

    Encrypts and decrypts secret values using GCP Cloud KMS symmetric
    keys. Key material never leaves GCP.

    Args:
        project_id: GCP project ID.
        location_id: KMS location (e.g. ``"us-east1"``).
        key_ring_id: KMS key ring name.
        client: Pre-configured ``KeyManagementServiceClient``. Uses
                default credentials if omitted.
    """

    def __init__(
        self,
        project_id: str,
        location_id: str,
        key_ring_id: str,
        *,
        client: Any | None = None,
    ) -> None:
        self._project = project_id
        self._location = location_id
        self._key_ring = key_ring_id
        self._client = client or google_kms.KeyManagementServiceClient()
        self._key_ring_path = self._client.key_ring_path(
            project_id, location_id, key_ring_id
        )

    @property
    def algorithm(self) -> str:
        return "gcp-kms"

    def generate_key(self) -> tuple[str, str]:
        """Create a new Cloud KMS symmetric key in the configured key ring.

        Returns ``(crypto_key_resource_name, "")`` — GCP manages the
        key material so no local material is returned.
        """
        import secrets as _secrets

        key_id = f"pyscoped-{_secrets.token_hex(8)}"
        crypto_key = {"purpose": google_kms.CryptoKey.CryptoKeyPurpose.ENCRYPT_DECRYPT}

        response = self._client.create_crypto_key(
            request={
                "parent": self._key_ring_path,
                "crypto_key_id": key_id,
                "crypto_key": crypto_key,
            }
        )
        return response.name, ""

    def encrypt(self, plaintext: str, *, key_id: str) -> str:
        response = self._client.encrypt(
            request={
                "name": key_id,
                "plaintext": plaintext.encode("utf-8"),
            }
        )
        return base64.b64encode(response.ciphertext).decode("ascii")

    def decrypt(self, ciphertext: str, *, key_id: str) -> str:
        response = self._client.decrypt(
            request={
                "name": key_id,
                "ciphertext": base64.b64decode(ciphertext),
            }
        )
        return response.plaintext.decode("utf-8")
