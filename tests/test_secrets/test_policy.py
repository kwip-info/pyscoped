"""Tests for secret policy management."""

from datetime import timedelta

import pytest

from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.secrets.backend import InMemoryBackend
from scoped.secrets.policy import SecretPolicyManager
from scoped.secrets.vault import SecretVault
from scoped.types import now_utc


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    return store.create_principal(kind="user", display_name="Alice", principal_id="alice")


@pytest.fixture
def objects(sqlite_backend):
    return ScopedManager(sqlite_backend)


@pytest.fixture
def vault(sqlite_backend, objects):
    enc = InMemoryBackend()
    return SecretVault(sqlite_backend, enc, object_manager=objects)


@pytest.fixture
def policies(sqlite_backend):
    return SecretPolicyManager(sqlite_backend)


class TestCreatePolicy:

    def test_create_for_secret(self, policies, vault, principals):
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=principals.id,
        )
        p = policies.create_policy(
            created_by=principals.id, secret_id=secret.id,
            max_age_seconds=86400,
        )
        assert p.secret_id == secret.id
        assert p.max_age_seconds == 86400

    def test_create_for_classification(self, policies, principals):
        p = policies.create_policy(
            created_by=principals.id, classification="critical",
            max_age_seconds=3600, auto_rotate=True,
        )
        assert p.classification == "critical"
        assert p.auto_rotate is True

    def test_create_with_scope_restrictions(self, policies, principals):
        p = policies.create_policy(
            created_by=principals.id, classification="sensitive",
            allowed_scopes=["s1", "s2"],
        )
        assert p.allowed_scopes == ["s1", "s2"]

    def test_get_policy(self, policies, principals):
        p = policies.create_policy(
            created_by=principals.id, classification="standard",
        )
        fetched = policies.get_policy(p.id)
        assert fetched is not None
        assert fetched.id == p.id

    def test_get_nonexistent(self, policies):
        assert policies.get_policy("nonexistent") is None


class TestGetPoliciesForSecret:

    def test_by_secret_id(self, policies, vault, principals):
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=principals.id,
        )
        policies.create_policy(
            created_by=principals.id, secret_id=secret.id, max_age_seconds=100,
        )
        result = policies.get_policies_for_secret(secret.id)
        assert len(result) == 1

    def test_by_classification(self, policies, vault, principals):
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=principals.id,
            classification="critical",
        )
        policies.create_policy(
            created_by=principals.id, classification="critical",
            max_age_seconds=3600,
        )
        result = policies.get_policies_for_secret(secret.id)
        assert len(result) == 1

    def test_no_policies(self, policies, vault, principals):
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=principals.id,
        )
        result = policies.get_policies_for_secret(secret.id)
        assert result == []

    def test_nonexistent_secret(self, policies):
        result = policies.get_policies_for_secret("nonexistent")
        assert result == []


class TestScopeAndEnvChecks:

    def test_scope_allowed_no_policy(self, policies, vault, principals):
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=principals.id,
        )
        assert policies.check_scope_allowed(secret.id, "any-scope") is True

    def test_scope_allowed(self, policies, vault, principals):
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=principals.id,
        )
        policies.create_policy(
            created_by=principals.id, secret_id=secret.id,
            allowed_scopes=["s1", "s2"],
        )
        assert policies.check_scope_allowed(secret.id, "s1") is True
        assert policies.check_scope_allowed(secret.id, "s3") is False

    def test_env_allowed_no_policy(self, policies, vault, principals):
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=principals.id,
        )
        assert policies.check_env_allowed(secret.id, "any-env") is True

    def test_env_allowed(self, policies, vault, principals):
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=principals.id,
        )
        policies.create_policy(
            created_by=principals.id, secret_id=secret.id,
            allowed_envs=["e1"],
        )
        assert policies.check_env_allowed(secret.id, "e1") is True
        assert policies.check_env_allowed(secret.id, "e2") is False


class TestNeedsRotation:

    def test_no_policy_no_rotation(self, policies, vault, principals):
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=principals.id,
        )
        assert policies.needs_rotation(secret.id) is False

    def test_nonexistent_secret(self, policies):
        assert policies.needs_rotation("nonexistent") is False

    def test_list_policies(self, policies, principals):
        policies.create_policy(
            created_by=principals.id, classification="critical",
        )
        policies.create_policy(
            created_by=principals.id, classification="standard",
        )
        result = policies.list_policies()
        assert len(result) == 2

    def test_list_by_classification(self, policies, principals):
        policies.create_policy(
            created_by=principals.id, classification="critical",
        )
        policies.create_policy(
            created_by=principals.id, classification="standard",
        )
        result = policies.list_policies(classification="critical")
        assert len(result) == 1
