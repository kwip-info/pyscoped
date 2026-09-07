"""Tests for secret encryption backends."""

import pytest

from scoped.secrets.backend import FernetBackend, InMemoryBackend


class TestInMemoryBackend:

    def test_generate_key(self):
        b = InMemoryBackend()
        key_id, material = b.generate_key()
        assert key_id == "test-key-1"
        assert material == "material-1"

    def test_encrypt_decrypt(self):
        b = InMemoryBackend()
        key_id, _ = b.generate_key()
        ct = b.encrypt("my-secret", key_id=key_id)
        pt = b.decrypt(ct, key_id=key_id)
        assert pt == "my-secret"

    def test_unknown_key(self):
        b = InMemoryBackend()
        with pytest.raises(ValueError, match="Unknown key_id"):
            b.encrypt("x", key_id="bad")

    def test_algorithm(self):
        b = InMemoryBackend()
        assert b.algorithm == "plaintext-test"


class TestFernetBackend:

    def test_generate_key(self):
        b = FernetBackend()
        key_id, material = b.generate_key()
        assert isinstance(key_id, str)
        assert isinstance(material, str)

    def test_encrypt_decrypt(self):
        b = FernetBackend()
        key_id, _ = b.generate_key()
        ct = b.encrypt("super-secret-value", key_id=key_id)
        assert ct != "super-secret-value"
        pt = b.decrypt(ct, key_id=key_id)
        assert pt == "super-secret-value"

    def test_different_keys(self):
        b = FernetBackend()
        k1, _ = b.generate_key()
        k2, _ = b.generate_key()
        ct = b.encrypt("secret", key_id=k1)
        # Can decrypt with correct key
        assert b.decrypt(ct, key_id=k1) == "secret"
        # Wrong key fails
        with pytest.raises(Exception):
            b.decrypt(ct, key_id=k2)

    def test_unknown_key(self):
        b = FernetBackend()
        with pytest.raises(ValueError, match="Unknown key_id"):
            b.encrypt("x", key_id="bad")

    def test_algorithm(self):
        b = FernetBackend()
        assert b.algorithm == "fernet"

    def test_load_key(self):
        b = FernetBackend()
        # Generate a key and save the material
        k1, material = b.generate_key()
        ct = b.encrypt("hello", key_id=k1)

        # Load into a new backend
        b2 = FernetBackend()
        b2.load_key("loaded", material)
        pt = b2.decrypt(ct, key_id="loaded")
        assert pt == "hello"
