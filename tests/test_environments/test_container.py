"""Tests for environment container (object tracking)."""

import pytest

from scoped.environments.container import EnvironmentContainer
from scoped.environments.lifecycle import EnvironmentLifecycle
from scoped.environments.models import ObjectOrigin
from scoped.exceptions import EnvironmentNotFoundError, EnvironmentStateError
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    return alice


@pytest.fixture
def lifecycle(sqlite_backend):
    return EnvironmentLifecycle(sqlite_backend)


@pytest.fixture
def container(sqlite_backend):
    return EnvironmentContainer(sqlite_backend)


@pytest.fixture
def objects(sqlite_backend):
    return ScopedManager(sqlite_backend)


@pytest.fixture
def active_env(lifecycle, principals):
    """An environment in ACTIVE state."""
    env = lifecycle.spawn(name="Test", owner_id=principals.id)
    return lifecycle.activate(env.id, actor_id=principals.id)


class TestAddObject:

    def test_add_created_object(self, container, active_env, objects, principals):
        obj, _ = objects.create(
            object_type="Doc", owner_id=principals.id, data={"title": "Test"},
        )
        eo = container.add_object(active_env.id, obj.id)
        assert eo.environment_id == active_env.id
        assert eo.object_id == obj.id
        assert eo.origin == ObjectOrigin.CREATED

    def test_project_in(self, container, active_env, objects, principals):
        obj, _ = objects.create(
            object_type="Doc", owner_id=principals.id, data={"title": "External"},
        )
        eo = container.project_in(active_env.id, obj.id)
        assert eo.origin == ObjectOrigin.PROJECTED

    def test_cannot_add_to_spawning_env(self, lifecycle, container, principals):
        env = lifecycle.spawn(name="E", owner_id=principals.id)
        with pytest.raises(EnvironmentStateError):
            container.add_object(env.id, "obj1")

    def test_cannot_add_to_suspended_env(
        self, lifecycle, container, principals,
    ):
        env = lifecycle.spawn(name="E", owner_id=principals.id)
        lifecycle.activate(env.id, actor_id=principals.id)
        lifecycle.suspend(env.id, actor_id=principals.id)
        with pytest.raises(EnvironmentStateError):
            container.add_object(env.id, "obj1")

    def test_cannot_add_to_nonexistent_env(self, container):
        with pytest.raises(EnvironmentNotFoundError):
            container.add_object("nonexistent", "obj1")


class TestRemoveObject:

    def test_remove_existing(self, container, active_env, objects, principals):
        obj, _ = objects.create(
            object_type="Doc", owner_id=principals.id, data={"t": "x"},
        )
        container.add_object(active_env.id, obj.id)
        assert container.remove_object(active_env.id, obj.id) is True
        assert not container.contains(active_env.id, obj.id)

    def test_remove_nonexistent(self, container, active_env):
        assert container.remove_object(active_env.id, "nonexistent") is False


class TestQuery:

    def test_list_objects(self, container, active_env, objects, principals):
        o1, _ = objects.create(object_type="Doc", owner_id=principals.id, data={"a": 1})
        o2, _ = objects.create(object_type="Doc", owner_id=principals.id, data={"b": 2})
        container.add_object(active_env.id, o1.id, origin=ObjectOrigin.CREATED)
        container.add_object(active_env.id, o2.id, origin=ObjectOrigin.PROJECTED)

        all_objs = container.list_objects(active_env.id)
        assert len(all_objs) == 2

    def test_list_filtered_by_origin(self, container, active_env, objects, principals):
        o1, _ = objects.create(object_type="Doc", owner_id=principals.id, data={"a": 1})
        o2, _ = objects.create(object_type="Doc", owner_id=principals.id, data={"b": 2})
        container.add_object(active_env.id, o1.id, origin=ObjectOrigin.CREATED)
        container.add_object(active_env.id, o2.id, origin=ObjectOrigin.PROJECTED)

        created = container.list_objects(active_env.id, origin=ObjectOrigin.CREATED)
        assert len(created) == 1
        assert created[0].object_id == o1.id

    def test_get_created_object_ids(self, container, active_env, objects, principals):
        o1, _ = objects.create(object_type="Doc", owner_id=principals.id, data={"a": 1})
        o2, _ = objects.create(object_type="Doc", owner_id=principals.id, data={"b": 2})
        container.add_object(active_env.id, o1.id, origin=ObjectOrigin.CREATED)
        container.add_object(active_env.id, o2.id, origin=ObjectOrigin.PROJECTED)

        ids = container.get_created_object_ids(active_env.id)
        assert ids == [o1.id]

    def test_get_projected_object_ids(self, container, active_env, objects, principals):
        o1, _ = objects.create(object_type="Doc", owner_id=principals.id, data={"a": 1})
        container.add_object(active_env.id, o1.id, origin=ObjectOrigin.PROJECTED)

        ids = container.get_projected_object_ids(active_env.id)
        assert ids == [o1.id]

    def test_contains(self, container, active_env, objects, principals):
        o1, _ = objects.create(object_type="Doc", owner_id=principals.id, data={"a": 1})
        assert not container.contains(active_env.id, o1.id)
        container.add_object(active_env.id, o1.id)
        assert container.contains(active_env.id, o1.id)

    def test_count(self, container, active_env, objects, principals):
        assert container.count(active_env.id) == 0
        o1, _ = objects.create(object_type="Doc", owner_id=principals.id, data={"a": 1})
        container.add_object(active_env.id, o1.id)
        assert container.count(active_env.id) == 1

    def test_empty_env(self, container, active_env):
        assert container.list_objects(active_env.id) == []
        assert container.count(active_env.id) == 0
