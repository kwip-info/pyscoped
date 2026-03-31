"""Tests for ScopedManager — the isolation-enforcing object manager."""

import pytest

from scoped.audit.writer import AuditWriter
from scoped.exceptions import AccessDeniedError, IsolationViolationError
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.types import ActionType, Lifecycle


@pytest.fixture
def principal_store(sqlite_backend, registry):
    return PrincipalStore(sqlite_backend)


@pytest.fixture
def alice(principal_store):
    return principal_store.create_principal(
        kind="user", display_name="Alice", principal_id="alice",
    )


@pytest.fixture
def bob(principal_store):
    return principal_store.create_principal(
        kind="user", display_name="Bob", principal_id="bob",
    )


@pytest.fixture
def manager(sqlite_backend):
    return ScopedManager(sqlite_backend)


@pytest.fixture
def audited_manager(sqlite_backend):
    writer = AuditWriter(sqlite_backend)
    return ScopedManager(sqlite_backend, audit_writer=writer), writer


# -----------------------------------------------------------------------
# Create
# -----------------------------------------------------------------------

class TestCreate:

    def test_create_returns_object_and_version(self, manager, alice):
        obj, ver = manager.create(
            object_type="Document", owner_id=alice.id,
            data={"title": "Hello"},
        )
        assert obj.object_type == "Document"
        assert obj.owner_id == alice.id
        assert obj.current_version == 1
        assert obj.is_active
        assert ver.version == 1
        assert ver.data == {"title": "Hello"}
        assert ver.created_by == alice.id
        assert ver.checksum != ""

    def test_create_persists(self, manager, alice):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={"k": "v"},
        )
        loaded = manager.get(obj.id, principal_id=alice.id)
        assert loaded is not None
        assert loaded.id == obj.id

    def test_create_with_registry_entry(self, manager, alice):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={},
            registry_entry_id="reg-123",
        )
        assert obj.registry_entry_id == "reg-123"

    def test_create_traces_audit(self, audited_manager, alice):
        mgr, writer = audited_manager
        mgr.create(
            object_type="Doc", owner_id=alice.id, data={"title": "Traced"},
        )
        assert writer.last_sequence == 1


# -----------------------------------------------------------------------
# Read / Isolation
# -----------------------------------------------------------------------

class TestReadIsolation:

    def test_owner_can_read(self, manager, alice):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={"x": 1},
        )
        found = manager.get(obj.id, principal_id=alice.id)
        assert found is not None
        assert found.id == obj.id

    def test_non_owner_cannot_read(self, manager, alice, bob):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={"x": 1},
        )
        assert manager.get(obj.id, principal_id=bob.id) is None

    def test_get_or_raise_owner(self, manager, alice):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={},
        )
        found = manager.get_or_raise(obj.id, principal_id=alice.id)
        assert found.id == obj.id

    def test_get_or_raise_non_owner(self, manager, alice, bob):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={},
        )
        with pytest.raises(AccessDeniedError):
            manager.get_or_raise(obj.id, principal_id=bob.id)

    def test_get_or_raise_missing(self, manager, alice):
        with pytest.raises(AccessDeniedError):
            manager.get_or_raise("nonexistent", principal_id=alice.id)

    def test_list_only_own_objects(self, manager, alice, bob):
        manager.create(object_type="Doc", owner_id=alice.id, data={"a": 1})
        manager.create(object_type="Doc", owner_id=alice.id, data={"a": 2})
        manager.create(object_type="Doc", owner_id=bob.id, data={"b": 1})

        alice_objs = manager.list_objects(principal_id=alice.id)
        bob_objs = manager.list_objects(principal_id=bob.id)
        assert len(alice_objs) == 2
        assert len(bob_objs) == 1

    def test_list_filter_by_type(self, manager, alice):
        manager.create(object_type="Doc", owner_id=alice.id, data={})
        manager.create(object_type="Task", owner_id=alice.id, data={})
        manager.create(object_type="Doc", owner_id=alice.id, data={})

        docs = manager.list_objects(principal_id=alice.id, object_type="Doc")
        assert len(docs) == 2

    def test_list_excludes_tombstoned_by_default(self, manager, alice):
        obj, _ = manager.create(object_type="Doc", owner_id=alice.id, data={})
        manager.create(object_type="Doc", owner_id=alice.id, data={})
        manager.tombstone(obj.id, principal_id=alice.id)

        objs = manager.list_objects(principal_id=alice.id)
        assert len(objs) == 1

    def test_list_includes_tombstoned_when_requested(self, manager, alice):
        obj, _ = manager.create(object_type="Doc", owner_id=alice.id, data={})
        manager.create(object_type="Doc", owner_id=alice.id, data={})
        manager.tombstone(obj.id, principal_id=alice.id)

        objs = manager.list_objects(principal_id=alice.id, include_tombstoned=True)
        assert len(objs) == 2

    def test_list_pagination(self, manager, alice):
        for i in range(5):
            manager.create(object_type="Doc", owner_id=alice.id, data={"i": i})

        page1 = manager.list_objects(principal_id=alice.id, limit=2, offset=0)
        page2 = manager.list_objects(principal_id=alice.id, limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2

    def test_count(self, manager, alice, bob):
        manager.create(object_type="Doc", owner_id=alice.id, data={})
        manager.create(object_type="Task", owner_id=alice.id, data={})
        manager.create(object_type="Doc", owner_id=bob.id, data={})

        assert manager.count(principal_id=alice.id) == 2
        assert manager.count(principal_id=alice.id, object_type="Doc") == 1
        assert manager.count(principal_id=bob.id) == 1


# -----------------------------------------------------------------------
# Update (versioning)
# -----------------------------------------------------------------------

class TestUpdate:

    def test_update_creates_new_version(self, manager, alice):
        obj, v1 = manager.create(
            object_type="Doc", owner_id=alice.id, data={"title": "V1"},
        )
        updated, v2 = manager.update(
            obj.id, principal_id=alice.id,
            data={"title": "V2"}, change_reason="updated title",
        )
        assert updated.current_version == 2
        assert v2.version == 2
        assert v2.data == {"title": "V2"}
        assert v2.change_reason == "updated title"

    def test_original_version_preserved(self, manager, alice):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={"title": "V1"},
        )
        manager.update(obj.id, principal_id=alice.id, data={"title": "V2"})

        v1 = manager.get_version(obj.id, 1)
        assert v1 is not None
        assert v1.data == {"title": "V1"}

    def test_update_non_owner_denied(self, manager, alice, bob):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={},
        )
        with pytest.raises(AccessDeniedError):
            manager.update(obj.id, principal_id=bob.id, data={"x": 1})

    def test_update_tombstoned_denied(self, manager, alice):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={},
        )
        manager.tombstone(obj.id, principal_id=alice.id)
        with pytest.raises(IsolationViolationError):
            manager.update(obj.id, principal_id=alice.id, data={"x": 1})

    def test_multiple_updates_chain(self, manager, alice):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={"v": 1},
        )
        manager.update(obj.id, principal_id=alice.id, data={"v": 2})
        updated, v3 = manager.update(
            obj.id, principal_id=alice.id, data={"v": 3},
        )
        assert updated.current_version == 3
        assert v3.version == 3

    def test_checksum_changes_with_data(self, manager, alice):
        obj, v1 = manager.create(
            object_type="Doc", owner_id=alice.id, data={"a": 1},
        )
        _, v2 = manager.update(
            obj.id, principal_id=alice.id, data={"a": 2},
        )
        assert v1.checksum != v2.checksum

    def test_update_traces_audit(self, audited_manager, alice):
        mgr, writer = audited_manager
        obj, _ = mgr.create(
            object_type="Doc", owner_id=alice.id, data={"old": True},
        )
        mgr.update(obj.id, principal_id=alice.id, data={"new": True})
        assert writer.last_sequence == 2  # create + update


# -----------------------------------------------------------------------
# Tombstone
# -----------------------------------------------------------------------

class TestTombstone:

    def test_tombstone_marks_archived(self, manager, alice):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={},
        )
        tomb = manager.tombstone(obj.id, principal_id=alice.id, reason="cleanup")
        assert tomb.object_id == obj.id
        assert tomb.reason == "cleanup"

        reloaded = manager.get(obj.id, principal_id=alice.id)
        assert reloaded is not None
        assert reloaded.is_tombstoned

    def test_tombstone_non_owner_denied(self, manager, alice, bob):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={},
        )
        with pytest.raises(AccessDeniedError):
            manager.tombstone(obj.id, principal_id=bob.id)

    def test_double_tombstone_denied(self, manager, alice):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={},
        )
        manager.tombstone(obj.id, principal_id=alice.id)
        with pytest.raises(IsolationViolationError):
            manager.tombstone(obj.id, principal_id=alice.id)

    def test_get_tombstone(self, manager, alice):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={},
        )
        manager.tombstone(obj.id, principal_id=alice.id, reason="done")
        tomb = manager.get_tombstone(obj.id)
        assert tomb is not None
        assert tomb.reason == "done"
        assert tomb.tombstoned_by == alice.id

    def test_no_tombstone_for_active(self, manager, alice):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={},
        )
        assert manager.get_tombstone(obj.id) is None

    def test_versions_preserved_after_tombstone(self, manager, alice):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={"v": 1},
        )
        manager.update(obj.id, principal_id=alice.id, data={"v": 2})
        manager.tombstone(obj.id, principal_id=alice.id)

        # Versions still accessible (needed for rollback/audit)
        v1 = manager.get_version(obj.id, 1)
        v2 = manager.get_version(obj.id, 2)
        assert v1 is not None
        assert v2 is not None

    def test_tombstone_traces_audit(self, audited_manager, alice):
        mgr, writer = audited_manager
        obj, _ = mgr.create(
            object_type="Doc", owner_id=alice.id, data={"x": 1},
        )
        mgr.tombstone(obj.id, principal_id=alice.id)
        assert writer.last_sequence == 2  # create + delete


# -----------------------------------------------------------------------
# Version access
# -----------------------------------------------------------------------

class TestVersionAccess:

    def test_get_version(self, manager, alice):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={"v": 1},
        )
        ver = manager.get_version(obj.id, 1)
        assert ver is not None
        assert ver.data == {"v": 1}

    def test_get_version_missing(self, manager, alice):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={},
        )
        assert manager.get_version(obj.id, 99) is None

    def test_get_current_version(self, manager, alice):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={"v": 1},
        )
        manager.update(obj.id, principal_id=alice.id, data={"v": 2})

        cur = manager.get_current_version(obj.id, principal_id=alice.id)
        assert cur is not None
        assert cur.version == 2
        assert cur.data == {"v": 2}

    def test_get_current_version_isolation(self, manager, alice, bob):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={},
        )
        assert manager.get_current_version(obj.id, principal_id=bob.id) is None

    def test_list_versions(self, manager, alice):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={"v": 1},
        )
        manager.update(obj.id, principal_id=alice.id, data={"v": 2})
        manager.update(obj.id, principal_id=alice.id, data={"v": 3})

        versions = manager.list_versions(obj.id, principal_id=alice.id)
        assert len(versions) == 3
        assert [v.version for v in versions] == [1, 2, 3]

    def test_list_versions_isolation(self, manager, alice, bob):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={},
        )
        assert manager.list_versions(obj.id, principal_id=bob.id) == []

    def test_diff(self, manager, alice):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={"title": "A", "x": 1},
        )
        manager.update(
            obj.id, principal_id=alice.id,
            data={"title": "B", "y": 2},
        )
        d = manager.diff(obj.id, 1, 2, principal_id=alice.id)
        assert d is not None
        assert d["changed"] == {"title": {"old": "A", "new": "B"}}
        assert d["added"] == {"y": 2}
        assert d["removed"] == {"x": 1}

    def test_diff_isolation(self, manager, alice, bob):
        obj, _ = manager.create(
            object_type="Doc", owner_id=alice.id, data={"a": 1},
        )
        assert manager.diff(obj.id, 1, 1, principal_id=bob.id) is None
