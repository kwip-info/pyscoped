"""Tests for integration connection management."""

import pytest

from scoped.exceptions import IntegrationError
from scoped.identity.principal import PrincipalStore
from scoped.integrations.connectors import IntegrationManager
from scoped.types import Lifecycle


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    return alice


@pytest.fixture
def manager(sqlite_backend):
    return IntegrationManager(sqlite_backend)


class TestCreateIntegration:

    def test_basic_create(self, manager, principals):
        i = manager.create_integration(
            name="github-acme", integration_type="github",
            owner_id=principals.id, description="Acme GitHub org",
        )
        assert i.name == "github-acme"
        assert i.integration_type == "github"
        assert i.is_active

    def test_create_with_config(self, manager, principals):
        i = manager.create_integration(
            name="slack-eng", integration_type="slack",
            owner_id=principals.id,
            config={"channel": "#engineering", "notify": True},
        )
        assert i.config["channel"] == "#engineering"

    def test_create_with_scope(self, manager, principals, sqlite_backend):
        from scoped.tenancy.models import Scope
        from scoped.types import generate_id, now_utc
        sid = generate_id()
        sqlite_backend.execute(
            "INSERT INTO scopes (id, name, owner_id, created_at, lifecycle) VALUES (?, ?, ?, ?, ?)",
            (sid, "test-scope", principals.id, now_utc().isoformat(), "ACTIVE"),
        )
        i = manager.create_integration(
            name="db", integration_type="database",
            owner_id=principals.id, scope_id=sid,
        )
        assert i.scope_id == sid

    def test_create_with_credentials_ref(self, manager, principals):
        i = manager.create_integration(
            name="api", integration_type="api",
            owner_id=principals.id, credentials_ref="ref-123",
        )
        assert i.credentials_ref == "ref-123"

    def test_create_with_metadata(self, manager, principals):
        i = manager.create_integration(
            name="custom", integration_type="custom",
            owner_id=principals.id, metadata={"env": "production"},
        )
        assert i.metadata["env"] == "production"


class TestGetIntegration:

    def test_get_existing(self, manager, principals):
        created = manager.create_integration(
            name="gh", integration_type="github", owner_id=principals.id,
        )
        fetched = manager.get_integration(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.name == "gh"

    def test_get_nonexistent(self, manager):
        assert manager.get_integration("nope") is None

    def test_get_or_raise(self, manager):
        with pytest.raises(IntegrationError, match="not found"):
            manager.get_integration_or_raise("nope")


class TestListIntegrations:

    def test_list_by_owner(self, manager, principals):
        manager.create_integration(
            name="i1", integration_type="github", owner_id=principals.id,
        )
        manager.create_integration(
            name="i2", integration_type="slack", owner_id=principals.id,
        )
        result = manager.list_integrations(owner_id=principals.id)
        assert len(result) == 2

    def test_list_by_type(self, manager, principals):
        manager.create_integration(
            name="i1", integration_type="github", owner_id=principals.id,
        )
        manager.create_integration(
            name="i2", integration_type="slack", owner_id=principals.id,
        )
        result = manager.list_integrations(integration_type="github")
        assert len(result) == 1

    def test_list_active_only(self, manager, principals):
        i = manager.create_integration(
            name="i1", integration_type="github", owner_id=principals.id,
        )
        manager.archive_integration(i.id, actor_id=principals.id)
        assert len(manager.list_integrations(active_only=True)) == 0
        assert len(manager.list_integrations(active_only=False)) == 1

    def test_list_by_scope(self, manager, principals, sqlite_backend):
        from scoped.types import generate_id, now_utc
        s1 = generate_id()
        s2 = generate_id()
        for sid in (s1, s2):
            sqlite_backend.execute(
                "INSERT INTO scopes (id, name, owner_id, created_at, lifecycle) VALUES (?, ?, ?, ?, ?)",
                (sid, f"scope-{sid[:4]}", principals.id, now_utc().isoformat(), "ACTIVE"),
            )
        manager.create_integration(
            name="i1", integration_type="github",
            owner_id=principals.id, scope_id=s1,
        )
        manager.create_integration(
            name="i2", integration_type="slack",
            owner_id=principals.id, scope_id=s2,
        )
        result = manager.list_integrations(scope_id=s1)
        assert len(result) == 1


class TestUpdateConfig:

    def test_update_config(self, manager, principals):
        i = manager.create_integration(
            name="gh", integration_type="github",
            owner_id=principals.id, config={"org": "acme"},
        )
        updated = manager.update_config(
            i.id, config={"org": "acme", "repo": "main"}, actor_id=principals.id,
        )
        assert updated.config["repo"] == "main"

        # Verify persistence
        fetched = manager.get_integration(i.id)
        assert fetched.config["repo"] == "main"


class TestArchiveIntegration:

    def test_archive(self, manager, principals):
        i = manager.create_integration(
            name="gh", integration_type="github", owner_id=principals.id,
        )
        manager.archive_integration(i.id, actor_id=principals.id)
        fetched = manager.get_integration(i.id)
        assert not fetched.is_active
        assert fetched.lifecycle == Lifecycle.ARCHIVED
