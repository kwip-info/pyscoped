"""Pluggable encryption backend for secret values.

Default implementation uses Fernet (AES-128-CBC with HMAC-SHA256).
Production deployments can swap to HSM, KMS, or HashiCorp Vault.
"""

from __future__ import annotations

import secrets
from abc import ABC, abstractmethod

from cryptography.fernet import Fernet


class SecretBackend(ABC):
    """Abstract encryption backend interface."""

    @abstractmethod
    def encrypt(self, plaintext: str, *, key_id: str) -> str:
        """Encrypt plaintext, return ciphertext string."""

    @abstractmethod
    def decrypt(self, ciphertext: str, *, key_id: str) -> str:
        """Decrypt ciphertext, return plaintext string."""

    @abstractmethod
    def generate_key(self) -> tuple[str, str]:
        """Generate a new encryption key. Returns (key_id, key_material)."""

    @property
    @abstractmethod
    def algorithm(self) -> str:
        """The encryption algorithm name."""


class FernetBackend(SecretBackend):
    """Default Fernet-based encryption backend.

    Stores keys in memory. For production, use a KMS-backed backend.
    """

    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}

    @property
    def algorithm(self) -> str:
        return "fernet"

    def generate_key(self) -> tuple[str, str]:
        key = Fernet.generate_key()
        key_id = secrets.token_hex(8)
        self._keys[key_id] = key
        return key_id, key.decode()

    def load_key(self, key_id: str, key_material: str) -> None:
        """Load a key into the backend."""
        self._keys[key_id] = key_material.encode()

    def encrypt(self, plaintext: str, *, key_id: str) -> str:
        key = self._keys.get(key_id)
        if key is None:
            raise ValueError(f"Unknown key_id: {key_id}")
        f = Fernet(key)
        return f.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str, *, key_id: str) -> str:
        key = self._keys.get(key_id)
        if key is None:
            raise ValueError(f"Unknown key_id: {key_id}")
        f = Fernet(key)
        return f.decrypt(ciphertext.encode()).decode()


class InMemoryBackend(SecretBackend):
    """Simple in-memory backend for testing. NOT for production."""

    def __init__(self) -> None:
        self._keys: dict[str, str] = {}
        self._counter = 0

    @property
    def algorithm(self) -> str:
        return "plaintext-test"

    def generate_key(self) -> tuple[str, str]:
        self._counter += 1
        key_id = f"test-key-{self._counter}"
        key_material = f"material-{self._counter}"
        self._keys[key_id] = key_material
        return key_id, key_material

    def load_key(self, key_id: str, key_material: str) -> None:
        self._keys[key_id] = key_material

    def encrypt(self, plaintext: str, *, key_id: str) -> str:
        if key_id not in self._keys:
            raise ValueError(f"Unknown key_id: {key_id}")
        # Simple reversible encoding for testing
        return f"ENC:{key_id}:{plaintext}"

    def decrypt(self, ciphertext: str, *, key_id: str) -> str:
        if key_id not in self._keys:
            raise ValueError(f"Unknown key_id: {key_id}")
        prefix = f"ENC:{key_id}:"
        if not ciphertext.startswith(prefix):
            raise ValueError("Invalid ciphertext for this key")
        return ciphertext[len(prefix):]
