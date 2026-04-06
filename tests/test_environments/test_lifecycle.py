"""Tests for environment lifecycle management."""

import pytest

from scoped.audit.writer import AuditWriter
from scoped.environments.container import EnvironmentContainer
from scoped.environments.lifecycle import EnvironmentLifecycle
from scoped.environments.models import EnvironmentState, ObjectOrigin
from scoped.exceptions import (
    AccessDeniedError,
    EnvironmentNotFoundError,
    EnvironmentStateError,
)
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    bob = store.create_principal(kind="user", display_name="Bob", principal_id="bob")
    return alice, bob


@pytest.fixture
def writer(sqlite_backend):
    return AuditWriter(sqlite_backend)


@pytest.fixture
def lifecycle(sqlite_backend, writer):
    return EnvironmentLifecycle(sqlite_backend, audit_writer=writer)


@pytest.fixture
def objects(sqlite_backend):
    return ScopedManager(sqlite_backend)


@pytest.fixture
def container(sqlite_backend, writer):
    return EnvironmentContainer(sqlite_backend, audit_writer=writer)


class TestSpawn:

    def test_spawn_creates_environment(self, lifecycle, principals):
        alice, _ = principals
        env = lifecycle.spawn(name="Test Env", owner_id=alice.id)
        assert env.name == "Test Env"
        assert env.state == EnvironmentState.SPAWNING
        assert env.owner_id == alice.id
        assert env.scope_id is not None
        assert env.ephemeral is True

    def test_spawn_creates_scope(self, lifecycle, principals, sqlite_backend):
        alice, _ = principals
        env = lifecycle.spawn(name="My Env", owner_id=alice.id)
        # Verify scope exists in DB
        row = sqlite_backend.fetch_one(
            "SELECT * FROM scopes WHERE id = ?", (env.scope_id,),
        )
        assert row is not None
        assert "env:My Env" in row["name"]

    def test_spawn_with_metadata(self, lifecycle, principals):
        alice, _ = principals
        env = lifecycle.spawn(
            name="E", owner_id=alice.id,
            metadata={"purpose": "testing"},
        )
        assert env.metadata == {"purpose": "testing"}

    def test_spawn_non_ephemeral(self, lifecycle, principals):
        alice, _ = principals
        env = lifecycle.spawn(name="Persistent", owner_id=alice.id, ephemeral=False)
        assert env.ephemeral is False

    def test_spawn_with_template_id(self, lifecycle, principals):
        alice, _ = principals
        tmpl = lifecycle.create_template(
            name="Review", owner_id=alice.id, config={"mode": "review"},
        )
        env = lifecycle.spawn(
            name="Review Instance", owner_id=alice.id, template_id=tmpl.id,
        )
        assert env.template_id == tmpl.id


class TestStateTransitions:

    def test_activate(self, lifecycle, principals):
        alice, _ = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        activated = lifecycle.activate(env.id, actor_id=alice.id)
        assert activated.state == EnvironmentState.ACTIVE

    def test_suspend(self, lifecycle, principals):
        alice, _ = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        lifecycle.activate(env.id, actor_id=alice.id)
        suspended = lifecycle.suspend(env.id, actor_id=alice.id)
        assert suspended.state == EnvironmentState.SUSPENDED

    def test_resume(self, lifecycle, principals):
        alice, _ = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        lifecycle.activate(env.id, actor_id=alice.id)
        lifecycle.suspend(env.id, actor_id=alice.id)
        resumed = lifecycle.resume(env.id, actor_id=alice.id)
        assert resumed.state == EnvironmentState.ACTIVE

    def test_complete(self, lifecycle, principals):
        alice, _ = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        lifecycle.activate(env.id, actor_id=alice.id)
        completed = lifecycle.complete(env.id, actor_id=alice.id)
        assert completed.state == EnvironmentState.COMPLETED

    def test_discard_from_completed(self, lifecycle, principals):
        alice, _ = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        lifecycle.activate(env.id, actor_id=alice.id)
        lifecycle.complete(env.id, actor_id=alice.id)
        discarded = lifecycle.discard(env.id, actor_id=alice.id)
        assert discarded.state == EnvironmentState.DISCARDED

    def test_promote(self, lifecycle, principals):
        alice, _ = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        lifecycle.activate(env.id, actor_id=alice.id)
        lifecycle.complete(env.id, actor_id=alice.id)
        promoted = lifecycle.promote(env.id, actor_id=alice.id)
        assert promoted.state == EnvironmentState.PROMOTED

    def test_discard_after_promote(self, lifecycle, principals):
        alice, _ = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        lifecycle.activate(env.id, actor_id=alice.id)
        lifecycle.complete(env.id, actor_id=alice.id)
        lifecycle.promote(env.id, actor_id=alice.id)
        discarded = lifecycle.discard(env.id, actor_id=alice.id)
        assert discarded.state == EnvironmentState.DISCARDED

    def test_invalid_transition_raises(self, lifecycle, principals):
        alice, _ = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        # Cannot go from SPAWNING to COMPLETED
        with pytest.raises(EnvironmentStateError):
            lifecycle.complete(env.id, actor_id=alice.id)

    def test_cannot_activate_completed(self, lifecycle, principals):
        alice, _ = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        lifecycle.activate(env.id, actor_id=alice.id)
        lifecycle.complete(env.id, actor_id=alice.id)
        with pytest.raises(EnvironmentStateError):
            lifecycle.activate(env.id, actor_id=alice.id)

    def test_discard_archives_scope(self, lifecycle, principals, sqlite_backend):
        alice, _ = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        scope_id = env.scope_id
        lifecycle.activate(env.id, actor_id=alice.id)
        lifecycle.complete(env.id, actor_id=alice.id)
        lifecycle.discard(env.id, actor_id=alice.id)

        row = sqlite_backend.fetch_one(
            "SELECT lifecycle FROM scopes WHERE id = ?", (scope_id,),
        )
        assert row["lifecycle"] == "ARCHIVED"


class TestGet:

    def test_get_existing(self, lifecycle, principals):
        alice, _ = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        fetched = lifecycle.get(env.id)
        assert fetched is not None
        assert fetched.id == env.id

    def test_get_nonexistent(self, lifecycle):
        assert lifecycle.get("nonexistent") is None

    def test_get_or_raise(self, lifecycle):
        with pytest.raises(EnvironmentNotFoundError):
            lifecycle.get_or_raise("nonexistent")


class TestListEnvironments:

    def test_list_all(self, lifecycle, principals):
        alice, _ = principals
        lifecycle.spawn(name="E1", owner_id=alice.id)
        lifecycle.spawn(name="E2", owner_id=alice.id)
        envs = lifecycle.list_environments()
        assert len(envs) == 2

    def test_filter_by_owner(self, lifecycle, principals):
        alice, bob = principals
        lifecycle.spawn(name="E1", owner_id=alice.id)
        lifecycle.spawn(name="E2", owner_id=bob.id)
        envs = lifecycle.list_environments(owner_id=alice.id)
        assert len(envs) == 1
        assert envs[0].owner_id == alice.id

    def test_filter_by_state(self, lifecycle, principals):
        alice, _ = principals
        e1 = lifecycle.spawn(name="E1", owner_id=alice.id)
        lifecycle.spawn(name="E2", owner_id=alice.id)
        lifecycle.activate(e1.id, actor_id=alice.id)

        active = lifecycle.list_environments(state=EnvironmentState.ACTIVE)
        assert len(active) == 1
        spawning = lifecycle.list_environments(state=EnvironmentState.SPAWNING)
        assert len(spawning) == 1

    def test_filter_by_ephemeral(self, lifecycle, principals):
        alice, _ = principals
        lifecycle.spawn(name="E1", owner_id=alice.id, ephemeral=True)
        lifecycle.spawn(name="E2", owner_id=alice.id, ephemeral=False)

        ephemeral = lifecycle.list_environments(ephemeral=True)
        assert len(ephemeral) == 1
        persistent = lifecycle.list_environments(ephemeral=False)
        assert len(persistent) == 1


class TestTemplates:

    def test_create_template(self, lifecycle, principals):
        alice, _ = principals
        tmpl = lifecycle.create_template(
            name="Review", owner_id=alice.id,
            description="Code review env",
            config={"rules": ["read_only"]},
        )
        assert tmpl.name == "Review"
        assert tmpl.config == {"rules": ["read_only"]}

    def test_get_template(self, lifecycle, principals):
        alice, _ = principals
        tmpl = lifecycle.create_template(name="T", owner_id=alice.id)
        fetched = lifecycle.get_template(tmpl.id)
        assert fetched is not None
        assert fetched.id == tmpl.id

    def test_get_nonexistent_template(self, lifecycle):
        assert lifecycle.get_template("nonexistent") is None

    def test_list_templates(self, lifecycle, principals):
        alice, _ = principals
        lifecycle.create_template(name="T1", owner_id=alice.id)
        lifecycle.create_template(name="T2", owner_id=alice.id)
        templates = lifecycle.list_templates()
        assert len(templates) == 2

    def test_list_templates_by_owner(self, lifecycle, principals):
        alice, bob = principals
        lifecycle.create_template(name="T1", owner_id=alice.id)
        lifecycle.create_template(name="T2", owner_id=bob.id)
        templates = lifecycle.list_templates(owner_id=alice.id)
        assert len(templates) == 1

    def test_spawn_from_template(self, lifecycle, principals):
        alice, _ = principals
        tmpl = lifecycle.create_template(
            name="Review", owner_id=alice.id,
            description="Code review",
            config={"mode": "review"},
        )
        env = lifecycle.spawn_from_template(tmpl.id, owner_id=alice.id)
        assert env.template_id == tmpl.id
        assert env.metadata.get("template_config") == {"mode": "review"}

    def test_spawn_from_nonexistent_template(self, lifecycle, principals):
        alice, _ = principals
        with pytest.raises(EnvironmentNotFoundError):
            lifecycle.spawn_from_template("nonexistent", owner_id=alice.id)

    def test_spawn_from_template_custom_name(self, lifecycle, principals):
        alice, _ = principals
        tmpl = lifecycle.create_template(name="T", owner_id=alice.id)
        env = lifecycle.spawn_from_template(
            tmpl.id, owner_id=alice.id, name="Custom Name",
        )
        assert env.name == "Custom Name"


class TestAccessControl:

    def test_non_owner_cannot_activate(self, lifecycle, principals):
        alice, bob = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        with pytest.raises(AccessDeniedError):
            lifecycle.activate(env.id, actor_id=bob.id)

    def test_non_owner_cannot_suspend(self, lifecycle, principals):
        alice, bob = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        lifecycle.activate(env.id, actor_id=alice.id)
        with pytest.raises(AccessDeniedError):
            lifecycle.suspend(env.id, actor_id=bob.id)

    def test_non_owner_cannot_complete(self, lifecycle, principals):
        alice, bob = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        lifecycle.activate(env.id, actor_id=alice.id)
        with pytest.raises(AccessDeniedError):
            lifecycle.complete(env.id, actor_id=bob.id)

    def test_non_owner_cannot_discard(self, lifecycle, principals):
        alice, bob = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        lifecycle.activate(env.id, actor_id=alice.id)
        lifecycle.complete(env.id, actor_id=alice.id)
        with pytest.raises(AccessDeniedError):
            lifecycle.discard(env.id, actor_id=bob.id)

    def test_non_owner_cannot_promote(self, lifecycle, principals):
        alice, bob = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        lifecycle.activate(env.id, actor_id=alice.id)
        lifecycle.complete(env.id, actor_id=alice.id)
        with pytest.raises(AccessDeniedError):
            lifecycle.promote(env.id, actor_id=bob.id)


class TestDiscardCascade:

    def test_discard_tombstones_created_objects(
        self, lifecycle, container, objects, principals, sqlite_backend,
    ):
        alice, _ = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        lifecycle.activate(env.id, actor_id=alice.id)

        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"x": 1},
        )
        container.add_object(env.id, obj.id, origin=ObjectOrigin.CREATED)

        lifecycle.complete(env.id, actor_id=alice.id)
        lifecycle.discard(env.id, actor_id=alice.id)

        row = sqlite_backend.fetch_one(
            "SELECT lifecycle FROM scoped_objects WHERE id = ?", (obj.id,),
        )
        assert row["lifecycle"] == "ARCHIVED"

    def test_discard_leaves_projected_objects_untouched(
        self, lifecycle, container, objects, principals, sqlite_backend,
    ):
        alice, _ = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        lifecycle.activate(env.id, actor_id=alice.id)

        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"x": 1},
        )
        container.add_object(env.id, obj.id, origin=ObjectOrigin.PROJECTED)

        lifecycle.complete(env.id, actor_id=alice.id)
        lifecycle.discard(env.id, actor_id=alice.id)

        row = sqlite_backend.fetch_one(
            "SELECT lifecycle FROM scoped_objects WHERE id = ?", (obj.id,),
        )
        assert row["lifecycle"] == "ACTIVE"


class TestAuditTrail:

    def test_spawn_emits_audit(self, lifecycle, writer, principals, sqlite_backend):
        alice, _ = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        rows = sqlite_backend.fetch_all(
            "SELECT * FROM audit_trail WHERE target_id = ? AND action = 'env_spawn'",
            (env.id,),
        )
        assert len(rows) == 1

    def test_transition_emits_audit(self, lifecycle, writer, principals, sqlite_backend):
        alice, _ = principals
        env = lifecycle.spawn(name="E", owner_id=alice.id)
        lifecycle.activate(env.id, actor_id=alice.id)
        rows = sqlite_backend.fetch_all(
            "SELECT * FROM audit_trail WHERE target_id = ? AND action = 'env_resume'",
            (env.id,),
        )
        assert len(rows) == 1
