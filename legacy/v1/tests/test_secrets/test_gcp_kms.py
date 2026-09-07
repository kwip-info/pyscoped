"""Tests for GCP Cloud KMS encryption backend (mocked — no real GCP calls)."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest

try:
    from google.cloud import kms  # noqa: F401

    _HAS_GCP = True
except ImportError:
    _HAS_GCP = False

pytestmark = pytest.mark.skipif(not _HAS_GCP, reason="google-cloud-kms not installed")


@pytest.fixture
def mock_kms_client():
    """A mocked GCP KMS client."""
    client = MagicMock()

    # key_ring_path mock
    client.key_ring_path.return_value = (
        "projects/test-project/locations/us-east1/keyRings/test-ring"
    )

    # create_crypto_key mock
    mock_key = MagicMock()
    mock_key.name = (
        "projects/test-project/locations/us-east1/keyRings/test-ring/"
        "cryptoKeys/pyscoped-abc123"
    )
    client.create_crypto_key.return_value = mock_key

    # encrypt mock
    def _encrypt(request):
        plaintext = request["plaintext"]
        if isinstance(plaintext, str):
            plaintext = plaintext.encode()
        resp = MagicMock()
        resp.ciphertext = b"GCP_ENC:" + plaintext
        return resp

    # decrypt mock
    def _decrypt(request):
        ct = request["ciphertext"]
        assert ct.startswith(b"GCP_ENC:")
        resp = MagicMock()
        resp.plaintext = ct[len(b"GCP_ENC:"):]
        return resp

    client.encrypt.side_effect = _encrypt
    client.decrypt.side_effect = _decrypt
    return client


@pytest.fixture
def gcp_backend(mock_kms_client):
    from scoped.secrets.gcp_kms import GCPKMSBackend

    return GCPKMSBackend(
        project_id="test-project",
        location_id="us-east1",
        key_ring_id="test-ring",
        client=mock_kms_client,
    )


class TestGCPKMSBackend:
    def test_algorithm(self, gcp_backend):
        assert gcp_backend.algorithm == "gcp-kms"

    def test_generate_key(self, gcp_backend, mock_kms_client):
        key_id, material = gcp_backend.generate_key()
        assert "test-project" in key_id
        assert "test-ring" in key_id
        assert material == ""
        mock_kms_client.create_crypto_key.assert_called_once()

    def test_encrypt_decrypt_round_trip(self, gcp_backend):
        key_name = "projects/test-project/locations/us-east1/keyRings/test-ring/cryptoKeys/k1"
        plaintext = "my-secret-value"

        ciphertext = gcp_backend.encrypt(plaintext, key_id=key_name)
        assert isinstance(ciphertext, str)
        base64.b64decode(ciphertext)

        result = gcp_backend.decrypt(ciphertext, key_id=key_name)
        assert result == plaintext

    def test_encrypt_calls_client(self, gcp_backend, mock_kms_client):
        gcp_backend.encrypt("test", key_id="key-1")
        mock_kms_client.encrypt.assert_called_once_with(
            request={"name": "key-1", "plaintext": b"test"}
        )

    def test_decrypt_calls_client(self, gcp_backend, mock_kms_client):
        ct = gcp_backend.encrypt("hello", key_id="key-1")
        mock_kms_client.decrypt.reset_mock()

        gcp_backend.decrypt(ct, key_id="key-1")
        mock_kms_client.decrypt.assert_called_once()

    def test_unicode_round_trip(self, gcp_backend):
        key_name = "key-1"
        plaintext = "héllo wörld 🔑"

        ct = gcp_backend.encrypt(plaintext, key_id=key_name)
        result = gcp_backend.decrypt(ct, key_id=key_name)
        assert result == plaintext
