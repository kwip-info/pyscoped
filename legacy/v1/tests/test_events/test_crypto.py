"""Tests for webhook config encryption/decryption (scoped.events.crypto)."""

from __future__ import annotations

import pytest

from scoped.events.crypto import (
    _SENSITIVE_KEYS,
    decrypt_config,
    encrypt_config,
    generate_webhook_key,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_key() -> bytes:
    return generate_webhook_key()


# ===========================================================================
# Round-trip
# ===========================================================================


class TestEncryptDecryptRoundTrip:
    def test_encrypt_decrypt_round_trip(self):
        """Headers are encrypted then decrypted back to the original value."""
        key = _make_key()
        config = {
            "headers": {"Authorization": "Bearer secret-token", "X-Custom": "value"},
            "url": "https://example.com/hook",
            "timeout": 30,
        }
        encrypted = encrypt_config(config, key)

        # Headers should be a ciphertext string after encryption
        assert isinstance(encrypted["headers"], str)
        assert encrypted["headers"] != config["headers"]

        # Non-sensitive keys unchanged
        assert encrypted["url"] == "https://example.com/hook"
        assert encrypted["timeout"] == 30

        # Decrypt recovers original
        decrypted = decrypt_config(encrypted, key)
        assert decrypted["headers"] == config["headers"]
        assert decrypted["url"] == "https://example.com/hook"
        assert decrypted["timeout"] == 30


class TestAuthTokenEncrypted:
    def test_auth_token_encrypted(self):
        """The auth_token field is encrypted at rest."""
        key = _make_key()
        config = {
            "auth_token": "sk-live-abc123xyz",
            "url": "https://hooks.example.com",
        }
        encrypted = encrypt_config(config, key)

        # auth_token should be an opaque ciphertext string
        assert isinstance(encrypted["auth_token"], str)
        assert encrypted["auth_token"] != config["auth_token"]

        # Round-trip restores the original value
        decrypted = decrypt_config(encrypted, key)
        assert decrypted["auth_token"] == config["auth_token"]


class TestNonSensitiveKeysUnchanged:
    def test_non_sensitive_keys_unchanged(self):
        """Keys not in _SENSITIVE_KEYS pass through without modification."""
        key = _make_key()
        config = {
            "url": "https://example.com/hook",
            "timeout": 10,
            "retry_count": 3,
            "method": "POST",
        }
        encrypted = encrypt_config(config, key)

        assert encrypted["url"] == config["url"]
        assert encrypted["timeout"] == config["timeout"]
        assert encrypted["retry_count"] == config["retry_count"]
        assert encrypted["method"] == config["method"]


class TestPlaintextBackwardCompat:
    def test_plaintext_backward_compat(self):
        """decrypt_config on a config with plaintext headers returns them as-is.

        This ensures backward compatibility with configs stored before
        encryption was enabled.
        """
        key = _make_key()
        config = {
            "headers": {"Authorization": "Bearer old-token"},
            "url": "https://legacy.example.com",
        }
        # Calling decrypt on plaintext config should not raise and should
        # return the headers dict unchanged (not a valid Fernet token).
        decrypted = decrypt_config(config, key)
        assert decrypted["headers"] == {"Authorization": "Bearer old-token"}
        assert decrypted["url"] == "https://legacy.example.com"

    def test_plaintext_auth_token_backward_compat(self):
        """Plaintext auth_token string that is not a Fernet token is preserved."""
        key = _make_key()
        config = {"auth_token": "plain-text-token"}
        decrypted = decrypt_config(config, key)
        assert decrypted["auth_token"] == "plain-text-token"


class TestEmptyConfig:
    def test_empty_config(self):
        """Encrypt/decrypt on an empty dict should not crash."""
        key = _make_key()
        encrypted = encrypt_config({}, key)
        assert encrypted == {}

        decrypted = decrypt_config({}, key)
        assert decrypted == {}

    def test_none_values_skipped(self):
        """Sensitive keys with None values are not encrypted."""
        key = _make_key()
        config = {"headers": None, "auth_token": None, "url": "https://x.com"}
        encrypted = encrypt_config(config, key)
        assert encrypted["headers"] is None
        assert encrypted["auth_token"] is None
        assert encrypted["url"] == "https://x.com"


class TestNestedHeadersPreserved:
    def test_nested_headers_preserved(self):
        """A dict of headers (including nested values) survives round-trip."""
        key = _make_key()
        config = {
            "headers": {
                "Authorization": "Bearer token-123",
                "X-Request-Id": "req-456",
                "X-Metadata": {"env": "production", "version": 2},
            },
        }
        encrypted = encrypt_config(config, key)
        assert isinstance(encrypted["headers"], str)

        decrypted = decrypt_config(encrypted, key)
        assert decrypted["headers"] == config["headers"]
        assert decrypted["headers"]["X-Metadata"]["env"] == "production"
        assert decrypted["headers"]["X-Metadata"]["version"] == 2


class TestSecretFieldEncrypted:
    def test_secret_field_encrypted(self):
        """The 'secret' key in _SENSITIVE_KEYS is also encrypted."""
        key = _make_key()
        config = {"secret": "webhook-signing-secret-xyz"}
        encrypted = encrypt_config(config, key)

        assert isinstance(encrypted["secret"], str)
        assert encrypted["secret"] != config["secret"]

        decrypted = decrypt_config(encrypted, key)
        assert decrypted["secret"] == config["secret"]


class TestWrongKeyFails:
    def test_wrong_key_leaves_encrypted_value(self):
        """Decrypting with a different key leaves the ciphertext as-is (no crash)."""
        key1 = _make_key()
        key2 = _make_key()
        config = {"headers": {"Authorization": "Bearer secret"}}

        encrypted = encrypt_config(config, key1)
        # Decrypt with wrong key -- should not raise, value left as-is
        decrypted = decrypt_config(encrypted, key2)
        # The headers value should still be the ciphertext string (not decrypted)
        assert isinstance(decrypted["headers"], str)
        assert decrypted["headers"] == encrypted["headers"]
