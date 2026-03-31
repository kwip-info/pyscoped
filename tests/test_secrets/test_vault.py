"""Tests for secret vault — create, rotate, refs, resolution."""

from datetime import timedelta

import pytest

from scoped.exceptions import (
    SecretAccessDeniedError,
    SecretNotFoundError,
    SecretRefExpiredError,
)
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.secrets.backend import InMemoryBackend
from scoped.secrets.models import AccessResult
from scoped.secrets.vault import SecretVault
from scoped.types import now_utc


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    bob = store.create_principal(kind="user", display_name="Bob", principal_id="bob")
    return alice, bob


@pytest.fixture
def objects(sqlite_backend):
    return ScopedManager(sqlite_backend)


@pytest.fixture
def vault(sqlite_backend, objects):
    enc = InMemoryBackend()
    return SecretVault(sqlite_backend, enc, object_manager=objects)


class TestCreateSecret:

    def test_basic_create(self, vault, principals):
        alice, _ = principals
        secret, version = vault.create_secret(
            name="api-key", plaintext_value="sk-12345",
            owner_id=alice.id, description="Test key",
        )
        assert secret.name == "api-key"
        assert secret.description == "Test key"
        assert secret.current_version == 1
        assert secret.is_active
        assert version.version == 1
        assert version.encrypted_value != "sk-12345"

    def test_create_with_classification(self, vault, principals):
        alice, _ = principals
        secret, _ = vault.create_secret(
            name="db-pass", plaintext_value="secret",
            owner_id=alice.id, classification="critical",
        )
        assert secret.classification.value == "critical"

    def test_get_secret(self, vault, principals):
        alice, _ = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=alice.id,
        )
        fetched = vault.get_secret(secret.id)
        assert fetched is not None
        assert fetched.id == secret.id

    def test_get_nonexistent(self, vault):
        assert vault.get_secret("nonexistent") is None

    def test_get_or_raise(self, vault):
        with pytest.raises(SecretNotFoundError):
            vault.get_secret_or_raise("nonexistent")

    def test_list_secrets(self, vault, principals):
        alice, _ = principals
        vault.create_secret(name="k1", plaintext_value="v1", owner_id=alice.id)
        vault.create_secret(name="k2", plaintext_value="v2", owner_id=alice.id)
        result = vault.list_secrets(owner_id=alice.id)
        assert len(result) == 2

    def test_list_by_classification(self, vault, principals):
        alice, _ = principals
        vault.create_secret(
            name="k1", plaintext_value="v1",
            owner_id=alice.id, classification="standard",
        )
        vault.create_secret(
            name="k2", plaintext_value="v2",
            owner_id=alice.id, classification="critical",
        )
        result = vault.list_secrets(classification="critical")
        assert len(result) == 1

    def test_archive_secret(self, vault, principals):
        alice, _ = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=alice.id,
        )
        vault.archive_secret(secret.id, actor_id=alice.id)
        result = vault.list_secrets(active_only=True)
        assert len(result) == 0


class TestRotation:

    def test_rotate(self, vault, principals):
        alice, _ = principals
        secret, v1 = vault.create_secret(
            name="k", plaintext_value="old-value", owner_id=alice.id,
        )
        v2 = vault.rotate(
            secret.id, new_value="new-value",
            rotated_by=alice.id, reason="scheduled rotation",
        )
        assert v2.version == 2
        assert v2.reason == "scheduled rotation"

        # Secret current_version updated
        updated = vault.get_secret(secret.id)
        assert updated.current_version == 2
        assert updated.last_rotated_at is not None

    def test_multiple_rotations(self, vault, principals):
        alice, _ = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v1", owner_id=alice.id,
        )
        vault.rotate(secret.id, new_value="v2", rotated_by=alice.id)
        vault.rotate(secret.id, new_value="v3", rotated_by=alice.id)

        versions = vault.get_versions(secret.id)
        assert len(versions) == 3
        assert versions[0].version == 3  # newest first

    def test_get_specific_version(self, vault, principals):
        alice, _ = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v1", owner_id=alice.id,
        )
        vault.rotate(secret.id, new_value="v2", rotated_by=alice.id)

        v1 = vault.get_version(secret.id, 1)
        assert v1 is not None
        assert v1.version == 1

        v2 = vault.get_version(secret.id, 2)
        assert v2 is not None
        assert v2.version == 2

    def test_rotate_nonexistent(self, vault, principals):
        alice, _ = principals
        with pytest.raises(SecretNotFoundError):
            vault.rotate("nonexistent", new_value="v", rotated_by=alice.id)


class TestRefs:

    def test_grant_ref(self, vault, principals):
        alice, bob = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=alice.id,
        )
        ref = vault.grant_ref(
            secret_id=secret.id, granted_to=bob.id, granted_by=alice.id,
        )
        assert ref.secret_id == secret.id
        assert ref.granted_to == bob.id
        assert ref.is_active

    def test_grant_with_scope(self, vault, principals):
        alice, bob = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=alice.id,
        )
        ref = vault.grant_ref(
            secret_id=secret.id, granted_to=bob.id, granted_by=alice.id,
            scope_id="scope-1",
        )
        assert ref.scope_id == "scope-1"

    def test_revoke_ref(self, vault, principals):
        alice, bob = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=alice.id,
        )
        ref = vault.grant_ref(
            secret_id=secret.id, granted_to=bob.id, granted_by=alice.id,
        )
        vault.revoke_ref(ref.id, revoked_by=alice.id)
        fetched = vault.get_ref(ref.id)
        assert not fetched.is_active

    def test_list_refs(self, vault, principals):
        alice, bob = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=alice.id,
        )
        vault.grant_ref(
            secret_id=secret.id, granted_to=bob.id, granted_by=alice.id,
        )
        vault.grant_ref(
            secret_id=secret.id, granted_to=alice.id, granted_by=alice.id,
        )
        refs = vault.list_refs(secret.id)
        assert len(refs) == 2

    def test_list_refs_active_only(self, vault, principals):
        alice, bob = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=alice.id,
        )
        ref = vault.grant_ref(
            secret_id=secret.id, granted_to=bob.id, granted_by=alice.id,
        )
        vault.revoke_ref(ref.id, revoked_by=alice.id)
        assert len(vault.list_refs(secret.id, active_only=True)) == 0
        assert len(vault.list_refs(secret.id, active_only=False)) == 1

    def test_archive_revokes_all_refs(self, vault, principals):
        alice, bob = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=alice.id,
        )
        vault.grant_ref(
            secret_id=secret.id, granted_to=bob.id, granted_by=alice.id,
        )
        vault.archive_secret(secret.id, actor_id=alice.id)
        refs = vault.list_refs(secret.id, active_only=True)
        assert len(refs) == 0


class TestResolve:

    def test_basic_resolve(self, vault, principals):
        alice, bob = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="the-secret", owner_id=alice.id,
        )
        ref = vault.grant_ref(
            secret_id=secret.id, granted_to=bob.id, granted_by=alice.id,
        )
        value = vault.resolve(ref.ref_token, accessor_id=bob.id)
        assert value == "the-secret"

    def test_resolve_after_rotation(self, vault, principals):
        alice, bob = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="old", owner_id=alice.id,
        )
        ref = vault.grant_ref(
            secret_id=secret.id, granted_to=bob.id, granted_by=alice.id,
        )
        vault.rotate(secret.id, new_value="new", rotated_by=alice.id)
        value = vault.resolve(ref.ref_token, accessor_id=bob.id)
        assert value == "new"

    def test_resolve_wrong_accessor(self, vault, principals):
        alice, bob = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=alice.id,
        )
        ref = vault.grant_ref(
            secret_id=secret.id, granted_to=bob.id, granted_by=alice.id,
        )
        with pytest.raises(SecretAccessDeniedError, match="does not match"):
            vault.resolve(ref.ref_token, accessor_id=alice.id)

    def test_resolve_revoked_ref(self, vault, principals):
        alice, bob = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=alice.id,
        )
        ref = vault.grant_ref(
            secret_id=secret.id, granted_to=bob.id, granted_by=alice.id,
        )
        vault.revoke_ref(ref.id, revoked_by=alice.id)
        with pytest.raises(SecretAccessDeniedError, match="revoked"):
            vault.resolve(ref.ref_token, accessor_id=bob.id)

    def test_resolve_expired_ref(self, vault, principals):
        alice, bob = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=alice.id,
        )
        past = now_utc() - timedelta(hours=1)
        ref = vault.grant_ref(
            secret_id=secret.id, granted_to=bob.id, granted_by=alice.id,
            expires_at=past,
        )
        with pytest.raises(SecretRefExpiredError):
            vault.resolve(ref.ref_token, accessor_id=bob.id)

    def test_resolve_wrong_scope(self, vault, principals):
        alice, bob = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=alice.id,
        )
        ref = vault.grant_ref(
            secret_id=secret.id, granted_to=bob.id, granted_by=alice.id,
            scope_id="scope-1",
        )
        with pytest.raises(SecretAccessDeniedError, match="scope"):
            vault.resolve(ref.ref_token, accessor_id=bob.id, scope_id="scope-2")

    def test_resolve_correct_scope(self, vault, principals):
        alice, bob = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="scoped-val", owner_id=alice.id,
        )
        ref = vault.grant_ref(
            secret_id=secret.id, granted_to=bob.id, granted_by=alice.id,
            scope_id="scope-1",
        )
        value = vault.resolve(ref.ref_token, accessor_id=bob.id, scope_id="scope-1")
        assert value == "scoped-val"

    def test_resolve_wrong_environment(self, vault, principals):
        alice, bob = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=alice.id,
        )
        ref = vault.grant_ref(
            secret_id=secret.id, granted_to=bob.id, granted_by=alice.id,
            environment_id="env-1",
        )
        with pytest.raises(SecretAccessDeniedError, match="environment"):
            vault.resolve(ref.ref_token, accessor_id=bob.id, environment_id="env-2")

    def test_resolve_invalid_token(self, vault, principals):
        _, bob = principals
        with pytest.raises(SecretAccessDeniedError, match="Invalid"):
            vault.resolve("bad-token", accessor_id=bob.id)


class TestAccessLog:

    def test_successful_access_logged(self, vault, principals):
        alice, bob = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=alice.id,
        )
        ref = vault.grant_ref(
            secret_id=secret.id, granted_to=bob.id, granted_by=alice.id,
        )
        vault.resolve(ref.ref_token, accessor_id=bob.id)
        log = vault.get_access_log(secret.id)
        assert len(log) == 1
        assert log[0].result == AccessResult.SUCCESS

    def test_denied_access_logged(self, vault, principals):
        alice, bob = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=alice.id,
        )
        ref = vault.grant_ref(
            secret_id=secret.id, granted_to=bob.id, granted_by=alice.id,
        )
        try:
            vault.resolve(ref.ref_token, accessor_id=alice.id)
        except Exception:
            pass
        log = vault.get_access_log(secret.id)
        assert len(log) == 1
        assert log[0].result == AccessResult.DENIED

    def test_expired_access_logged(self, vault, principals):
        alice, bob = principals
        secret, _ = vault.create_secret(
            name="k", plaintext_value="v", owner_id=alice.id,
        )
        past = now_utc() - timedelta(hours=1)
        ref = vault.grant_ref(
            secret_id=secret.id, granted_to=bob.id, granted_by=alice.id,
            expires_at=past,
        )
        try:
            vault.resolve(ref.ref_token, accessor_id=bob.id)
        except Exception:
            pass
        log = vault.get_access_log(secret.id)
        assert len(log) == 1
        assert log[0].result == AccessResult.EXPIRED
