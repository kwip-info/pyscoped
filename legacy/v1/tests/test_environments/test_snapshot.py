"""Tests for environment snapshots."""

import pytest

from scoped.audit.writer import AuditWriter
from scoped.environments.container import EnvironmentContainer
from scoped.environments.lifecycle import EnvironmentLifecycle
from scoped.environments.models import ObjectOrigin
from scoped.environments.snapshot import SnapshotManager
from scoped.exceptions import AccessDeniedError
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
def container(sqlite_backend):
    return EnvironmentContainer(sqlite_backend)


@pytest.fixture
def snapshots(sqlite_backend, writer):
    return SnapshotManager(sqlite_backend, audit_writer=writer)


@pytest.fixture
def objects(sqlite_backend):
    return ScopedManager(sqlite_backend)


@pytest.fixture
def active_env(lifecycle, principals):
    alice, _ = principals
    env = lifecycle.spawn(name="Test", owner_id=alice.id)
    return lifecycle.activate(env.id, actor_id=alice.id)


class TestCapture:

    def test_capture_empty_env(self, snapshots, active_env, principals):
        snap = snapshots.capture(active_env.id, created_by=principals[0].id, name="v1")
        assert snap.environment_id == active_env.id
        assert snap.name == "v1"
        assert snap.checksum != ""
        assert snap.snapshot_data["environment"] is not None
        assert snap.snapshot_data["objects"] == []

    def test_capture_with_objects(
        self, snapshots, container, active_env, objects, principals,
    ):
        obj, _ = objects.create(
            object_type="Doc", owner_id=principals[0].id,
            data={"title": "Hello"},
        )
        container.add_object(active_env.id, obj.id)

        snap = snapshots.capture(active_env.id, created_by=principals[0].id)
        assert len(snap.snapshot_data["objects"]) == 1
        assert len(snap.snapshot_data["versions"]) == 1

    def test_capture_includes_memberships(
        self, snapshots, active_env, principals,
    ):
        snap = snapshots.capture(active_env.id, created_by=principals[0].id)
        # The owner is automatically a member of the env scope
        assert len(snap.snapshot_data["memberships"]) >= 1

    def test_capture_nonexistent_env(self, snapshots, principals):
        """Capturing a nonexistent env fails due to FK constraint."""
        with pytest.raises(Exception, match="(?i)integrity|foreign key"):
            snapshots.capture("nonexistent", created_by=principals[0].id)


class TestGet:

    def test_get_existing(self, snapshots, active_env, principals):
        snap = snapshots.capture(active_env.id, created_by=principals[0].id)
        fetched = snapshots.get(snap.id)
        assert fetched is not None
        assert fetched.id == snap.id
        assert fetched.checksum == snap.checksum

    def test_get_nonexistent(self, snapshots):
        assert snapshots.get("nonexistent") is None


class TestListSnapshots:

    def test_list_for_env(self, snapshots, active_env, principals):
        snapshots.capture(active_env.id, created_by=principals[0].id, name="v1")
        snapshots.capture(active_env.id, created_by=principals[0].id, name="v2")

        results = snapshots.list_snapshots(active_env.id)
        assert len(results) == 2
        # Newest first
        assert results[0].name == "v2"

    def test_list_empty(self, snapshots, active_env):
        assert snapshots.list_snapshots(active_env.id) == []


class TestVerify:

    def test_valid_checksum(self, snapshots, active_env, principals):
        snap = snapshots.capture(active_env.id, created_by=principals[0].id)
        assert snapshots.verify(snap.id) is True

    def test_nonexistent_snapshot(self, snapshots):
        assert snapshots.verify("nonexistent") is False

    def test_tampered_checksum(self, snapshots, active_env, principals, sqlite_backend):
        snap = snapshots.capture(active_env.id, created_by=principals[0].id)
        # Tamper with the checksum
        sqlite_backend.execute(
            "UPDATE environment_snapshots SET checksum = 'tampered' WHERE id = ?",
            (snap.id,),
        )
        assert snapshots.verify(snap.id) is False


class TestRestore:

    def test_restore_resets_object_version(
        self, snapshots, container, active_env, objects, principals, sqlite_backend,
    ):
        alice, _ = principals
        obj, v1 = objects.create(
            object_type="Doc", owner_id=alice.id, data={"v": 1},
        )
        container.add_object(active_env.id, obj.id)

        # Capture snapshot at version 1
        snap = snapshots.capture(active_env.id, created_by=alice.id, name="v1")

        # Update object to version 2
        objects.update(obj.id, data={"v": 2}, principal_id=alice.id)
        row = sqlite_backend.fetch_one(
            "SELECT current_version FROM scoped_objects WHERE id = ?", (obj.id,),
        )
        assert row["current_version"] == 2

        # Restore to snapshot
        snapshots.restore(snap.id, restored_by=alice.id)

        row = sqlite_backend.fetch_one(
            "SELECT current_version FROM scoped_objects WHERE id = ?", (obj.id,),
        )
        assert row["current_version"] == 1

    def test_restore_syncs_environment_objects(
        self, snapshots, container, active_env, objects, principals,
    ):
        alice, _ = principals
        o1, _ = objects.create(object_type="Doc", owner_id=alice.id, data={"a": 1})
        container.add_object(active_env.id, o1.id)

        snap = snapshots.capture(active_env.id, created_by=alice.id)

        # Add a second object after snapshot
        o2, _ = objects.create(object_type="Doc", owner_id=alice.id, data={"b": 2})
        container.add_object(active_env.id, o2.id)
        assert container.count(active_env.id) == 2

        # Remove original object
        container.remove_object(active_env.id, o1.id)
        assert container.count(active_env.id) == 1

        # Restore — should have only o1
        snapshots.restore(snap.id, restored_by=alice.id)
        assert container.contains(active_env.id, o1.id)
        assert not container.contains(active_env.id, o2.id)
        assert container.count(active_env.id) == 1

    def test_restore_nonexistent_snapshot_raises(self, snapshots, principals):
        alice, _ = principals
        with pytest.raises(ValueError, match="not found"):
            snapshots.restore("nonexistent", restored_by=alice.id)

    def test_restore_tampered_snapshot_raises(
        self, snapshots, active_env, principals, sqlite_backend,
    ):
        alice, _ = principals
        snap = snapshots.capture(active_env.id, created_by=alice.id)
        sqlite_backend.execute(
            "UPDATE environment_snapshots SET checksum = 'tampered' WHERE id = ?",
            (snap.id,),
        )
        with pytest.raises(ValueError, match="corrupted"):
            snapshots.restore(snap.id, restored_by=alice.id)


class TestSnapshotAccessControl:

    def test_non_owner_cannot_capture(self, snapshots, active_env, principals):
        _, bob = principals
        with pytest.raises(AccessDeniedError):
            snapshots.capture(active_env.id, created_by=bob.id)

    def test_non_owner_cannot_restore(
        self, snapshots, active_env, principals,
    ):
        alice, bob = principals
        snap = snapshots.capture(active_env.id, created_by=alice.id)
        with pytest.raises(AccessDeniedError):
            snapshots.restore(snap.id, restored_by=bob.id)


class TestSnapshotAudit:

    def test_capture_emits_audit(
        self, snapshots, active_env, principals, sqlite_backend,
    ):
        alice, _ = principals
        snapshots.capture(active_env.id, created_by=alice.id)
        rows = sqlite_backend.fetch_all(
            "SELECT * FROM audit_trail WHERE target_type = 'environment_snapshot'",
            (),
        )
        assert len(rows) >= 1

    def test_restore_emits_audit(
        self, snapshots, container, active_env, objects, principals, sqlite_backend,
    ):
        alice, _ = principals
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"x": 1},
        )
        container.add_object(active_env.id, obj.id)
        snap = snapshots.capture(active_env.id, created_by=alice.id)
        snapshots.restore(snap.id, restored_by=alice.id)

        rows = sqlite_backend.fetch_all(
            "SELECT * FROM audit_trail WHERE target_type = 'environment_snapshot' AND action = 'rollback'",
            (),
        )
        assert len(rows) == 1


class TestRetention:

    def test_retain_max_snapshots(self, snapshots, active_env, principals):
        alice, _ = principals
        for i in range(5):
            snapshots.capture(active_env.id, created_by=alice.id, name=f"v{i}")
        assert len(snapshots.list_snapshots(active_env.id)) == 5

        deleted = snapshots.apply_retention(active_env.id, max_snapshots=2)
        assert deleted == 3
        remaining = snapshots.list_snapshots(active_env.id)
        assert len(remaining) == 2
        # Newest kept
        assert remaining[0].name == "v4"
        assert remaining[1].name == "v3"

    def test_retain_no_op_when_under_limit(self, snapshots, active_env, principals):
        alice, _ = principals
        snapshots.capture(active_env.id, created_by=alice.id)
        deleted = snapshots.apply_retention(active_env.id, max_snapshots=10)
        assert deleted == 0

    def test_retain_no_params_deletes_nothing(self, snapshots, active_env, principals):
        alice, _ = principals
        snapshots.capture(active_env.id, created_by=alice.id)
        deleted = snapshots.apply_retention(active_env.id)
        assert deleted == 0
