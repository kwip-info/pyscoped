"""Tests for environment snapshots."""

import pytest

from scoped.environments.container import EnvironmentContainer
from scoped.environments.lifecycle import EnvironmentLifecycle
from scoped.environments.models import ObjectOrigin
from scoped.environments.snapshot import SnapshotManager
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
def snapshots(sqlite_backend):
    return SnapshotManager(sqlite_backend)


@pytest.fixture
def objects(sqlite_backend):
    return ScopedManager(sqlite_backend)


@pytest.fixture
def active_env(lifecycle, principals):
    env = lifecycle.spawn(name="Test", owner_id=principals.id)
    return lifecycle.activate(env.id, actor_id=principals.id)


class TestCapture:

    def test_capture_empty_env(self, snapshots, active_env, principals):
        snap = snapshots.capture(active_env.id, created_by=principals.id, name="v1")
        assert snap.environment_id == active_env.id
        assert snap.name == "v1"
        assert snap.checksum != ""
        assert snap.snapshot_data["environment"] is not None
        assert snap.snapshot_data["objects"] == []

    def test_capture_with_objects(
        self, snapshots, container, active_env, objects, principals,
    ):
        obj, _ = objects.create(
            object_type="Doc", owner_id=principals.id,
            data={"title": "Hello"},
        )
        container.add_object(active_env.id, obj.id)

        snap = snapshots.capture(active_env.id, created_by=principals.id)
        assert len(snap.snapshot_data["objects"]) == 1
        assert len(snap.snapshot_data["versions"]) == 1

    def test_capture_includes_memberships(
        self, snapshots, active_env, principals,
    ):
        snap = snapshots.capture(active_env.id, created_by=principals.id)
        # The owner is automatically a member of the env scope
        assert len(snap.snapshot_data["memberships"]) >= 1

    def test_capture_nonexistent_env(self, snapshots, principals):
        """Capturing a nonexistent env fails due to FK constraint."""
        with pytest.raises(Exception, match="(?i)integrity|foreign key"):
            snapshots.capture("nonexistent", created_by=principals.id)


class TestGet:

    def test_get_existing(self, snapshots, active_env, principals):
        snap = snapshots.capture(active_env.id, created_by=principals.id)
        fetched = snapshots.get(snap.id)
        assert fetched is not None
        assert fetched.id == snap.id
        assert fetched.checksum == snap.checksum

    def test_get_nonexistent(self, snapshots):
        assert snapshots.get("nonexistent") is None


class TestListSnapshots:

    def test_list_for_env(self, snapshots, active_env, principals):
        snapshots.capture(active_env.id, created_by=principals.id, name="v1")
        snapshots.capture(active_env.id, created_by=principals.id, name="v2")

        results = snapshots.list_snapshots(active_env.id)
        assert len(results) == 2
        # Newest first
        assert results[0].name == "v2"

    def test_list_empty(self, snapshots, active_env):
        assert snapshots.list_snapshots(active_env.id) == []


class TestVerify:

    def test_valid_checksum(self, snapshots, active_env, principals):
        snap = snapshots.capture(active_env.id, created_by=principals.id)
        assert snapshots.verify(snap.id) is True

    def test_nonexistent_snapshot(self, snapshots):
        assert snapshots.verify("nonexistent") is False

    def test_tampered_checksum(self, snapshots, active_env, principals, sqlite_backend):
        snap = snapshots.capture(active_env.id, created_by=principals.id)
        # Tamper with the checksum
        sqlite_backend.execute(
            "UPDATE environment_snapshots SET checksum = 'tampered' WHERE id = ?",
            (snap.id,),
        )
        assert snapshots.verify(snap.id) is False
