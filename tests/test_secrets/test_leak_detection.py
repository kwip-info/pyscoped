"""Tests for secret leak detection."""

import pytest

from scoped.exceptions import SecretLeakDetectedError
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.secrets.backend import InMemoryBackend
from scoped.secrets.leak_detection import LeakDetector
from scoped.secrets.vault import SecretVault


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    return store.create_principal(kind="user", display_name="Alice", principal_id="alice")


@pytest.fixture
def objects(sqlite_backend):
    return ScopedManager(sqlite_backend)


@pytest.fixture
def encryption():
    return InMemoryBackend()


@pytest.fixture
def vault(sqlite_backend, objects, encryption):
    return SecretVault(sqlite_backend, encryption, object_manager=objects)


@pytest.fixture
def detector(sqlite_backend, encryption):
    return LeakDetector(sqlite_backend, encryption)


class TestScanData:

    def test_no_secrets(self, detector):
        leaks = detector.scan_data({"key": "value"})
        assert leaks == []

    def test_detect_leaked_value(self, detector, vault, principals):
        vault.create_secret(
            name="k", plaintext_value="super-secret-123",
            owner_id=principals.id,
        )
        leaks = detector.scan_data({"data": "contains super-secret-123 here"})
        assert len(leaks) == 1
        assert "data" in leaks[0]

    def test_detect_in_nested_dict(self, detector, vault, principals):
        vault.create_secret(
            name="k", plaintext_value="my-api-key",
            owner_id=principals.id,
        )
        leaks = detector.scan_data({
            "config": {"credentials": {"api_key": "my-api-key"}},
        })
        assert len(leaks) >= 1

    def test_detect_in_list(self, detector, vault, principals):
        vault.create_secret(
            name="k", plaintext_value="list-secret",
            owner_id=principals.id,
        )
        leaks = detector.scan_data({"items": ["safe", "list-secret", "also safe"]})
        assert len(leaks) >= 1

    def test_no_leak_with_different_values(self, detector, vault, principals):
        vault.create_secret(
            name="k", plaintext_value="the-secret",
            owner_id=principals.id,
        )
        leaks = detector.scan_data({"data": "totally harmless text"})
        assert leaks == []

    def test_scan_with_provided_known_values(self, detector):
        known = {"known-value"}
        leaks = detector.scan_data(
            {"field": "contains known-value"},
            known_values=known,
        )
        assert len(leaks) == 1

    def test_empty_known_values(self, detector):
        leaks = detector.scan_data(
            {"field": "anything"},
            known_values=set(),
        )
        assert leaks == []


class TestScanOrRaise:

    def test_no_leak(self, detector):
        detector.scan_or_raise({"safe": "data"}, known_values=set())

    def test_leak_raises(self, detector, vault, principals):
        vault.create_secret(
            name="k", plaintext_value="leaked-value",
            owner_id=principals.id,
        )
        with pytest.raises(SecretLeakDetectedError, match="audit trail"):
            detector.scan_or_raise(
                {"field": "leaked-value"},
                context="audit trail",
            )

    def test_raise_with_context(self, detector):
        with pytest.raises(SecretLeakDetectedError, match="snapshot"):
            detector.scan_or_raise(
                {"data": "secret-val"},
                context="snapshot",
                known_values={"secret-val"},
            )


class TestGetKnownValues:

    def test_returns_current_values(self, detector, vault, principals):
        vault.create_secret(
            name="k1", plaintext_value="val1", owner_id=principals.id,
        )
        vault.create_secret(
            name="k2", plaintext_value="val2", owner_id=principals.id,
        )
        values = detector.get_known_values()
        assert "val1" in values
        assert "val2" in values

    def test_empty_when_no_secrets(self, detector):
        values = detector.get_known_values()
        assert values == set()
