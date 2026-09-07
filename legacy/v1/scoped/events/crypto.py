"""Encrypt/decrypt sensitive fields in webhook endpoint configs.

Sensitive keys (headers, auth_token) are encrypted at rest using Fernet.
Non-Fernet values are treated as plaintext for backward compatibility
with existing configs.
"""

from __future__ import annotations

from typing import Any

from cryptography.fernet import Fernet, InvalidToken

# Keys in config whose values should be encrypted
_SENSITIVE_KEYS = frozenset({"headers", "auth_token", "secret"})


def generate_webhook_key() -> bytes:
    """Generate a new Fernet key for webhook config encryption."""
    return Fernet.generate_key()


def encrypt_config(config: dict[str, Any], key: bytes) -> dict[str, Any]:
    """Encrypt sensitive fields in a webhook config dict.

    Non-sensitive keys are passed through unchanged.
    Values under sensitive keys are JSON-serialized then Fernet-encrypted.
    """
    import json

    f = Fernet(key)
    result = dict(config)
    for k in _SENSITIVE_KEYS:
        if k in result and result[k]:
            plaintext = json.dumps(result[k]).encode()
            result[k] = f.encrypt(plaintext).decode()
    return result


def decrypt_config(config: dict[str, Any], key: bytes) -> dict[str, Any]:
    """Decrypt sensitive fields in a webhook config dict.

    Values that are not valid Fernet tokens are returned as-is
    (backward compatibility with plaintext configs).
    """
    import json

    f = Fernet(key)
    result = dict(config)
    for k in _SENSITIVE_KEYS:
        if k in result and isinstance(result[k], str):
            try:
                decrypted = f.decrypt(result[k].encode())
                result[k] = json.loads(decrypted)
            except InvalidToken:
                pass  # Not encrypted (legacy plaintext) — leave as-is
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass  # Decrypted but not valid JSON — leave as-is
    return result
