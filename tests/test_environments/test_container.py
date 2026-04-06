"""Tests for environment container (object tracking)."""

import pytest

from scoped.audit.writer import AuditWriter
from scoped.environments.container import EnvironmentContainer
from scoped.environments.lifecycle import EnvironmentLifecycle
from scoped.environments.models import ObjectOrigin
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
def lifecycle(sqlite_backend):
    return EnvironmentLifecycle(sqlite_backend)


@pytest.fixture
def container(sqlite_backend, writer):
    return EnvironmentContainer(sqlite_backend, audit_writer=writer)


@pytest.fixture
def objects(sqlite_backend):
    return ScopedManager(sqlite_backend)


@pytest.fixture
def active_env(lifecycle, principals):
    """An environment in ACTIVE state."""
    alice, _ = principals
    env = lifecycle.spawn(name="Test", owner_id=alice.id)
    return lifecycle.activate(env.id, actor_id=alice.id)


class TestAddObject:

    def test_add_created_object(self, container, active_env, objects, principals):
        obj, _ = objects.create(
            object_type="Doc", owner_id=principals[0].id, data={"title": "Test"},
        )
        eo = container.add_object(active_env.id, obj.id)
        assert eo.environment_id == active_env.id
        assert eo.object_id == obj.id
        assert eo.origin == ObjectOrigin.CREATED

    def test_project_in(self, container, active_env, objects, principals):
        obj, _ = objects.create(
            object_type="Doc", owner_id=principals[0].id, data={"title": "External"},
        )
        eo = container.project_in(active_env.id, obj.id)
        assert eo.origin == ObjectOrigin.PROJECTED

    def test_cannot_add_to_spawning_env(self, lifecycle, container, principals):
        env = lifecycle.spawn(name="E", owner_id=principals[0].id)
        with pytest.raises(EnvironmentStateError):
            container.add_object(env.id, "obj1")

    def test_cannot_add_to_suspended_env(
        self, lifecycle, container, principals,
    ):
        env = lifecycle.spawn(name="E", owner_id=principals[0].id)
        lifecycle.activate(env.id, actor_id=principals[0].id)
        lifecycle.suspend(env.id, actor_id=principals[0].id)
        with pytest.raises(EnvironmentStateError):
            container.add_object(env.id, "obj1")

    def test_cannot_add_to_nonexistent_env(self, container):
        with pytest.raises(EnvironmentNotFoundError):
            container.add_object("nonexistent", "obj1")


class TestRemoveObject:

    def test_remove_existing(self, container, active_env, objects, principals):
        obj, _ = objects.create(
            object_type="Doc", owner_id=principals[0].id, data={"t": "x"},
        )
        container.add_object(active_env.id, obj.id)
        assert container.remove_object(active_env.id, obj.id) is True
        assert not container.contains(active_env.id, obj.id)

    def test_remove_nonexistent(self, container, active_env):
        assert container.remove_object(active_env.id, "nonexistent") is False


class TestQuery:

    def test_list_objects(self, container, active_env, objects, principals):
        o1, _ = objects.create(object_type="Doc", owner_id=principals[0].id, data={"a": 1})
        o2, _ = objects.create(object_type="Doc", owner_id=principals[0].id, data={"b": 2})
        container.add_object(active_env.id, o1.id, origin=ObjectOrigin.CREATED)
        container.add_object(active_env.id, o2.id, origin=ObjectOrigin.PROJECTED)

        all_objs = container.list_objects(active_env.id)
        assert len(all_objs) == 2

    def test_list_filtered_by_origin(self, container, active_env, objects, principals):
        o1, _ = objects.create(object_type="Doc", owner_id=principals[0].id, data={"a": 1})
        o2, _ = objects.create(object_type="Doc", owner_id=principals[0].id, data={"b": 2})
        container.add_object(active_env.id, o1.id, origin=ObjectOrigin.CREATED)
        container.add_object(active_env.id, o2.id, origin=ObjectOrigin.PROJECTED)

        created = container.list_objects(active_env.id, origin=ObjectOrigin.CREATED)
        assert len(created) == 1
        assert created[0].object_id == o1.id

    def test_get_created_object_ids(self, container, active_env, objects, principals):
        o1, _ = objects.create(object_type="Doc", owner_id=principals[0].id, data={"a": 1})
        o2, _ = objects.create(object_type="Doc", owner_id=principals[0].id, data={"b": 2})
        container.add_object(active_env.id, o1.id, origin=ObjectOrigin.CREATED)
        container.add_object(active_env.id, o2.id, origin=ObjectOrigin.PROJECTED)

        ids = container.get_created_object_ids(active_env.id)
        assert ids == [o1.id]

    def test_get_projected_object_ids(self, container, active_env, objects, principals):
        o1, _ = objects.create(object_type="Doc", owner_id=principals[0].id, data={"a": 1})
        container.add_object(active_env.id, o1.id, origin=ObjectOrigin.PROJECTED)

        ids = container.get_projected_object_ids(active_env.id)
        assert ids == [o1.id]

    def test_contains(self, container, active_env, objects, principals):
        o1, _ = objects.create(object_type="Doc", owner_id=principals[0].id, data={"a": 1})
        assert not container.contains(active_env.id, o1.id)
        container.add_object(active_env.id, o1.id)
        assert container.contains(active_env.id, o1.id)

    def test_count(self, container, active_env, objects, principals):
        assert container.count(active_env.id) == 0
        o1, _ = objects.create(object_type="Doc", owner_id=principals[0].id, data={"a": 1})
        container.add_object(active_env.id, o1.id)
        assert container.count(active_env.id) == 1

    def test_empty_env(self, container, active_env):
        assert container.list_objects(active_env.id) == []
        assert container.count(active_env.id) == 0


class TestContainerAccessControl:

    def test_non_owner_cannot_add_object(self, container, active_env, objects, principals):
        alice, bob = principals
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"x": 1},
        )
        with pytest.raises(AccessDeniedError):
            container.add_object(active_env.id, obj.id, actor_id=bob.id)

    def test_owner_can_add_object(self, container, active_env, objects, principals):
        alice, _ = principals
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"x": 1},
        )
        eo = container.add_object(active_env.id, obj.id, actor_id=alice.id)
        assert eo.object_id == obj.id

    def test_non_owner_cannot_remove_object(self, container, active_env, objects, principals):
        alice, bob = principals
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"x": 1},
        )
        container.add_object(active_env.id, obj.id)
        with pytest.raises(AccessDeniedError):
            container.remove_object(active_env.id, obj.id, actor_id=bob.id)


class TestContainerAudit:

    def test_add_object_emits_audit(
        self, container, active_env, objects, principals, sqlite_backend,
    ):
        alice, _ = principals
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"x": 1},
        )
        container.add_object(active_env.id, obj.id, actor_id=alice.id)

        rows = sqlite_backend.fetch_all(
            "SELECT * FROM audit_trail WHERE target_type = 'environment_object'",
            (),
        )
        assert len(rows) >= 1

    def test_remove_object_emits_audit(
        self, container, active_env, objects, principals, sqlite_backend,
    ):
        alice, _ = principals
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"x": 1},
        )
        container.add_object(active_env.id, obj.id, actor_id=alice.id)
        container.remove_object(active_env.id, obj.id, actor_id=alice.id)

        rows = sqlite_backend.fetch_all(
            "SELECT * FROM audit_trail WHERE target_type = 'environment_object'",
            (),
        )
        # add + remove = 2 entries
        assert len(rows) == 2
