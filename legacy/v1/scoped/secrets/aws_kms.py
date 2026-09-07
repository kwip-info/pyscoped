"""AWS KMS encryption backend for Layer 11 secrets.

Uses AWS Key Management Service for server-side encryption.
Requires ``boto3`` — install via ``pip install pyscoped[aws]``.
"""

from __future__ import annotations

import base64
from typing import Any

try:
    import boto3
except ImportError as exc:
    raise ImportError(
        "AWS KMS backend requires boto3. Install with: pip install pyscoped[aws]"
    ) from exc

from scoped.secrets.backend import SecretBackend


class AWSKMSBackend(SecretBackend):
    """AWS KMS encryption backend.

    Encrypts and decrypts secret values using AWS KMS symmetric keys.
    Key material never leaves KMS — all crypto operations happen
    server-side.

    Args:
        region_name: AWS region (e.g. ``"us-east-1"``). Uses default if
                     omitted.
        aws_access_key_id: Explicit access key. Falls back to
                           environment / IAM role if omitted.
        aws_secret_access_key: Explicit secret key.
        kms_client: Pre-configured ``boto3`` KMS client. If provided,
                    credential arguments are ignored.
    """

    def __init__(
        self,
        *,
        region_name: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        kms_client: Any | None = None,
    ) -> None:
        if kms_client is not None:
            self._kms = kms_client
        else:
            kwargs: dict[str, str] = {}
            if region_name:
                kwargs["region_name"] = region_name
            if aws_access_key_id:
                kwargs["aws_access_key_id"] = aws_access_key_id
            if aws_secret_access_key:
                kwargs["aws_secret_access_key"] = aws_secret_access_key
            self._kms = boto3.client("kms", **kwargs)

    @property
    def algorithm(self) -> str:
        return "aws-kms"

    def generate_key(self) -> tuple[str, str]:
        """Create a new KMS symmetric key.

        Returns ``(key_arn, "")`` — AWS manages the key material so no
        local material is returned.
        """
        response = self._kms.create_key(
            Description="pyscoped secret key",
            KeyUsage="ENCRYPT_DECRYPT",
            KeySpec="SYMMETRIC_DEFAULT",
        )
        key_arn = response["KeyMetadata"]["Arn"]
        return key_arn, ""

    def encrypt(self, plaintext: str, *, key_id: str) -> str:
        response = self._kms.encrypt(
            KeyId=key_id,
            Plaintext=plaintext.encode("utf-8"),
        )
        return base64.b64encode(response["CiphertextBlob"]).decode("ascii")

    def decrypt(self, ciphertext: str, *, key_id: str) -> str:
        response = self._kms.decrypt(
            KeyId=key_id,
            CiphertextBlob=base64.b64decode(ciphertext),
        )
        return response["Plaintext"].decode("utf-8")
