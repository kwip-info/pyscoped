"""Tests for AWS KMS encryption backend (mocked — no real AWS calls)."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_kms_client():
    """A mocked boto3 KMS client."""
    client = MagicMock()

    # generate_key mock
    client.create_key.return_value = {
        "KeyMetadata": {
            "Arn": "arn:aws:kms:us-east-1:123456789:key/test-key-id",
            "KeyId": "test-key-id",
        }
    }

    # encrypt/decrypt round-trip via side_effect
    def _encrypt(**kwargs):
        plaintext = kwargs["Plaintext"]
        if isinstance(plaintext, str):
            plaintext = plaintext.encode()
        return {"CiphertextBlob": b"ENCRYPTED:" + plaintext}

    def _decrypt(**kwargs):
        blob = kwargs["CiphertextBlob"]
        assert blob.startswith(b"ENCRYPTED:")
        return {"Plaintext": blob[len(b"ENCRYPTED:"):]}

    client.encrypt.side_effect = _encrypt
    client.decrypt.side_effect = _decrypt
    return client


@pytest.fixture
def aws_backend(mock_kms_client):
    from scoped.secrets.aws_kms import AWSKMSBackend

    return AWSKMSBackend(kms_client=mock_kms_client)


class TestAWSKMSBackend:
    def test_algorithm(self, aws_backend):
        assert aws_backend.algorithm == "aws-kms"

    def test_generate_key(self, aws_backend, mock_kms_client):
        key_id, material = aws_backend.generate_key()
        assert key_id == "arn:aws:kms:us-east-1:123456789:key/test-key-id"
        assert material == ""
        mock_kms_client.create_key.assert_called_once()

    def test_encrypt_decrypt_round_trip(self, aws_backend):
        key_arn = "arn:aws:kms:us-east-1:123456789:key/test-key-id"
        plaintext = "my-secret-value"

        ciphertext = aws_backend.encrypt(plaintext, key_id=key_arn)
        assert isinstance(ciphertext, str)
        # Should be base64-encoded
        base64.b64decode(ciphertext)

        result = aws_backend.decrypt(ciphertext, key_id=key_arn)
        assert result == plaintext

    def test_encrypt_calls_kms(self, aws_backend, mock_kms_client):
        aws_backend.encrypt("test", key_id="key-1")
        mock_kms_client.encrypt.assert_called_once_with(
            KeyId="key-1",
            Plaintext=b"test",
        )

    def test_decrypt_calls_kms(self, aws_backend, mock_kms_client):
        # First encrypt to get valid ciphertext
        ct = aws_backend.encrypt("hello", key_id="key-1")
        mock_kms_client.decrypt.reset_mock()

        aws_backend.decrypt(ct, key_id="key-1")
        mock_kms_client.decrypt.assert_called_once()

    def test_unicode_round_trip(self, aws_backend):
        key_arn = "key-1"
        plaintext = "héllo wörld 🔑"

        ct = aws_backend.encrypt(plaintext, key_id=key_arn)
        result = aws_backend.decrypt(ct, key_id=key_arn)
        assert result == plaintext
