"""Tests for ScopeLifecycle — create, freeze, archive, membership."""

import pytest

from scoped.exceptions import ScopeFrozenError, ScopeNotFoundError
from scoped.identity.principal import PrincipalStore
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.tenancy.models import ScopeRole
from scoped.types import Lifecycle


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    bob = store.create_principal(kind="user", display_name="Bob", principal_id="bob")
    carol = store.create_principal(kind="user", display_name="Carol", principal_id="carol")
    return alice, bob, carol


@pytest.fixture
def lifecycle(sqlite_backend):
    return ScopeLifecycle(sqlite_backend)


class TestCreateScope:

    def test_create_returns_scope(self, lifecycle, principals):
        alice, _, _ = principals
        scope = lifecycle.create_scope(name="Team A", owner_id=alice.id)
        assert scope.name == "Team A"
        assert scope.owner_id == alice.id
        assert scope.is_active

    def test_create_with_description_and_metadata(self, lifecycle, principals):
        alice, _, _ = principals
        scope = lifecycle.create_scope(
            name="S", owner_id=alice.id,
            description="A scope", metadata={"key": "val"},
        )
        assert scope.description == "A scope"
        assert scope.metadata == {"key": "val"}

    def test_create_auto_adds_owner_member(self, lifecycle, principals):
        alice, _, _ = principals
        scope = lifecycle.create_scope(name="S", owner_id=alice.id)
        members = lifecycle.get_memberships(scope.id)
        assert len(members) == 1
        assert members[0].principal_id == alice.id
        assert members[0].role == ScopeRole.OWNER

    def test_create_with_parent(self, lifecycle, principals):
        alice, _, _ = principals
        parent = lifecycle.create_scope(name="Org", owner_id=alice.id)
        child = lifecycle.create_scope(
            name="Team", owner_id=alice.id, parent_scope_id=parent.id,
        )
        assert child.parent_scope_id == parent.id

    def test_create_persists(self, lifecycle, principals):
        alice, _, _ = principals
        scope = lifecycle.create_scope(name="S", owner_id=alice.id)
        loaded = lifecycle.get_scope(scope.id)
        assert loaded is not None
        assert loaded.name == "S"


class TestGetScope:

    def test_get_existing(self, lifecycle, principals):
        alice, _, _ = principals
        scope = lifecycle.create_scope(name="S", owner_id=alice.id)
        assert lifecycle.get_scope(scope.id) is not None

    def test_get_missing_returns_none(self, lifecycle):
        assert lifecycle.get_scope("nonexistent") is None

    def test_get_or_raise_missing(self, lifecycle):
        with pytest.raises(ScopeNotFoundError):
            lifecycle.get_scope_or_raise("nonexistent")


class TestListScopes:

    def test_list_all(self, lifecycle, principals):
        alice, bob, _ = principals
        lifecycle.create_scope(name="S1", owner_id=alice.id)
        lifecycle.create_scope(name="S2", owner_id=bob.id)
        assert len(lifecycle.list_scopes()) == 2

    def test_list_by_owner(self, lifecycle, principals):
        alice, bob, _ = principals
        lifecycle.create_scope(name="S1", owner_id=alice.id)
        lifecycle.create_scope(name="S2", owner_id=bob.id)
        assert len(lifecycle.list_scopes(owner_id=alice.id)) == 1

    def test_list_excludes_archived(self, lifecycle, principals):
        alice, _, _ = principals
        s = lifecycle.create_scope(name="S1", owner_id=alice.id)
        lifecycle.create_scope(name="S2", owner_id=alice.id)
        lifecycle.archive_scope(s.id, archived_by=alice.id)
        assert len(lifecycle.list_scopes()) == 1

    def test_list_includes_archived_when_requested(self, lifecycle, principals):
        alice, _, _ = principals
        s = lifecycle.create_scope(name="S1", owner_id=alice.id)
        lifecycle.create_scope(name="S2", owner_id=alice.id)
        lifecycle.archive_scope(s.id, archived_by=alice.id)
        assert len(lifecycle.list_scopes(include_archived=True)) == 2


class TestMembership:

    def test_add_member(self, lifecycle, principals):
        alice, bob, _ = principals
        scope = lifecycle.create_scope(name="S", owner_id=alice.id)
        mem = lifecycle.add_member(
            scope.id, principal_id=bob.id,
            role=ScopeRole.EDITOR, granted_by=alice.id,
        )
        assert mem.principal_id == bob.id
        assert mem.role == ScopeRole.EDITOR
        assert mem.is_active

    def test_multiple_roles(self, lifecycle, principals):
        alice, bob, _ = principals
        scope = lifecycle.create_scope(name="S", owner_id=alice.id)
        lifecycle.add_member(scope.id, principal_id=bob.id, role=ScopeRole.VIEWER, granted_by=alice.id)
        lifecycle.add_member(scope.id, principal_id=bob.id, role=ScopeRole.EDITOR, granted_by=alice.id)
        members = lifecycle.get_memberships(scope.id)
        bob_roles = [m.role for m in members if m.principal_id == bob.id]
        assert ScopeRole.VIEWER in bob_roles
        assert ScopeRole.EDITOR in bob_roles

    def test_revoke_member(self, lifecycle, principals):
        alice, bob, _ = principals
        scope = lifecycle.create_scope(name="S", owner_id=alice.id)
        lifecycle.add_member(scope.id, principal_id=bob.id, granted_by=alice.id)

        count = lifecycle.revoke_member(scope.id, principal_id=bob.id, revoked_by=alice.id)
        assert count == 1
        assert not lifecycle.is_member(scope.id, bob.id)

    def test_revoke_specific_role(self, lifecycle, principals):
        alice, bob, _ = principals
        scope = lifecycle.create_scope(name="S", owner_id=alice.id)
        lifecycle.add_member(scope.id, principal_id=bob.id, role=ScopeRole.VIEWER, granted_by=alice.id)
        lifecycle.add_member(scope.id, principal_id=bob.id, role=ScopeRole.EDITOR, granted_by=alice.id)

        count = lifecycle.revoke_member(
            scope.id, principal_id=bob.id, revoked_by=alice.id, role=ScopeRole.VIEWER,
        )
        assert count == 1
        assert lifecycle.is_member(scope.id, bob.id)  # still editor

    def test_revoke_nonexistent_returns_zero(self, lifecycle, principals):
        alice, bob, _ = principals
        scope = lifecycle.create_scope(name="S", owner_id=alice.id)
        count = lifecycle.revoke_member(scope.id, principal_id=bob.id, revoked_by=alice.id)
        assert count == 0

    def test_is_member(self, lifecycle, principals):
        alice, bob, _ = principals
        scope = lifecycle.create_scope(name="S", owner_id=alice.id)
        assert lifecycle.is_member(scope.id, alice.id)  # auto-added as owner
        assert not lifecycle.is_member(scope.id, bob.id)

    def test_get_principal_scopes(self, lifecycle, principals):
        alice, bob, _ = principals
        s1 = lifecycle.create_scope(name="S1", owner_id=alice.id)
        s2 = lifecycle.create_scope(name="S2", owner_id=alice.id)
        lifecycle.add_member(s1.id, principal_id=bob.id, granted_by=alice.id)
        lifecycle.add_member(s2.id, principal_id=bob.id, granted_by=alice.id)

        bob_scopes = lifecycle.get_principal_scopes(bob.id)
        scope_ids = {m.scope_id for m in bob_scopes}
        assert s1.id in scope_ids
        assert s2.id in scope_ids

    def test_add_member_frozen_scope_raises(self, lifecycle, principals):
        alice, bob, _ = principals
        scope = lifecycle.create_scope(name="S", owner_id=alice.id)
        lifecycle.freeze_scope(scope.id, frozen_by=alice.id)

        with pytest.raises(ScopeFrozenError):
            lifecycle.add_member(scope.id, principal_id=bob.id, granted_by=alice.id)

    def test_revoke_member_frozen_scope_raises(self, lifecycle, principals):
        alice, _, _ = principals
        scope = lifecycle.create_scope(name="S", owner_id=alice.id)
        lifecycle.freeze_scope(scope.id, frozen_by=alice.id)

        with pytest.raises(ScopeFrozenError):
            lifecycle.revoke_member(scope.id, principal_id=alice.id, revoked_by=alice.id)


class TestLifecycleTransitions:

    def test_freeze(self, lifecycle, principals):
        alice, _, _ = principals
        scope = lifecycle.create_scope(name="S", owner_id=alice.id)
        frozen = lifecycle.freeze_scope(scope.id, frozen_by=alice.id)
        assert frozen.is_frozen

    def test_freeze_non_active_raises(self, lifecycle, principals):
        alice, _, _ = principals
        scope = lifecycle.create_scope(name="S", owner_id=alice.id)
        lifecycle.freeze_scope(scope.id, frozen_by=alice.id)
        with pytest.raises(ScopeFrozenError):
            lifecycle.freeze_scope(scope.id, frozen_by=alice.id)

    def test_archive(self, lifecycle, principals):
        alice, bob, _ = principals
        scope = lifecycle.create_scope(name="S", owner_id=alice.id)
        lifecycle.add_member(scope.id, principal_id=bob.id, granted_by=alice.id)

        archived = lifecycle.archive_scope(scope.id, archived_by=alice.id)
        assert archived.is_archived

    def test_archive_revokes_all_memberships(self, lifecycle, principals):
        alice, bob, carol = principals
        scope = lifecycle.create_scope(name="S", owner_id=alice.id)
        lifecycle.add_member(scope.id, principal_id=bob.id, granted_by=alice.id)
        lifecycle.add_member(scope.id, principal_id=carol.id, granted_by=alice.id)

        lifecycle.archive_scope(scope.id, archived_by=alice.id)

        active = lifecycle.get_memberships(scope.id, active_only=True)
        assert len(active) == 0

        all_members = lifecycle.get_memberships(scope.id, active_only=False)
        assert len(all_members) == 3  # alice + bob + carol (all archived)

    def test_archive_already_archived_raises(self, lifecycle, principals):
        alice, _, _ = principals
        scope = lifecycle.create_scope(name="S", owner_id=alice.id)
        lifecycle.archive_scope(scope.id, archived_by=alice.id)
        with pytest.raises(ScopeFrozenError):
            lifecycle.archive_scope(scope.id, archived_by=alice.id)

    def test_archive_frozen_scope(self, lifecycle, principals):
        """Can archive a frozen scope (dissolve it)."""
        alice, _, _ = principals
        scope = lifecycle.create_scope(name="S", owner_id=alice.id)
        lifecycle.freeze_scope(scope.id, frozen_by=alice.id)
        archived = lifecycle.archive_scope(scope.id, archived_by=alice.id)
        assert archived.is_archived
