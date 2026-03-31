"""Tests for VisibilityEngine — "what can principal X see?" """

import pytest

from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.tenancy.engine import VisibilityEngine
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.tenancy.models import AccessLevel, ScopeRole
from scoped.tenancy.projection import ProjectionManager


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    bob = store.create_principal(kind="user", display_name="Bob", principal_id="bob")
    carol = store.create_principal(kind="user", display_name="Carol", principal_id="carol")
    return alice, bob, carol


@pytest.fixture
def lc(sqlite_backend):
    return ScopeLifecycle(sqlite_backend)


@pytest.fixture
def mgr(sqlite_backend):
    return ScopedManager(sqlite_backend)


@pytest.fixture
def proj(sqlite_backend):
    return ProjectionManager(sqlite_backend)


@pytest.fixture
def engine(sqlite_backend):
    return VisibilityEngine(sqlite_backend)


class TestOwnerVisibility:

    def test_owner_sees_own_objects(self, mgr, engine, principals):
        alice, _, _ = principals
        o1, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={})
        o2, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={})

        visible = engine.visible_object_ids(alice.id)
        assert o1.id in visible
        assert o2.id in visible

    def test_non_owner_sees_nothing(self, mgr, engine, principals):
        alice, bob, _ = principals
        mgr.create(object_type="Doc", owner_id=alice.id, data={})

        visible = engine.visible_object_ids(bob.id)
        assert len(visible) == 0

    def test_can_see_own_object(self, mgr, engine, principals):
        alice, bob, _ = principals
        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={})

        assert engine.can_see(alice.id, obj.id)
        assert not engine.can_see(bob.id, obj.id)


class TestProjectionVisibility:

    def test_member_sees_projected_object(self, mgr, lc, proj, engine, principals):
        alice, bob, _ = principals
        scope = lc.create_scope(name="S", owner_id=alice.id)
        lc.add_member(scope.id, principal_id=bob.id, granted_by=alice.id)

        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={})
        proj.project(scope_id=scope.id, object_id=obj.id, projected_by=alice.id)

        assert engine.can_see(bob.id, obj.id)
        visible = engine.visible_object_ids(bob.id)
        assert obj.id in visible

    def test_non_member_cannot_see_projected(self, mgr, lc, proj, engine, principals):
        alice, _, carol = principals
        scope = lc.create_scope(name="S", owner_id=alice.id)
        # carol is NOT a member

        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={})
        proj.project(scope_id=scope.id, object_id=obj.id, projected_by=alice.id)

        assert not engine.can_see(carol.id, obj.id)

    def test_revoked_projection_invisible(self, mgr, lc, proj, engine, principals):
        alice, bob, _ = principals
        scope = lc.create_scope(name="S", owner_id=alice.id)
        lc.add_member(scope.id, principal_id=bob.id, granted_by=alice.id)

        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={})
        proj.project(scope_id=scope.id, object_id=obj.id, projected_by=alice.id)
        proj.revoke_projection(scope_id=scope.id, object_id=obj.id, revoked_by=alice.id)

        assert not engine.can_see(bob.id, obj.id)

    def test_revoked_membership_invisible(self, mgr, lc, proj, engine, principals):
        alice, bob, _ = principals
        scope = lc.create_scope(name="S", owner_id=alice.id)
        lc.add_member(scope.id, principal_id=bob.id, granted_by=alice.id)

        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={})
        proj.project(scope_id=scope.id, object_id=obj.id, projected_by=alice.id)

        lc.revoke_member(scope.id, principal_id=bob.id, revoked_by=alice.id)
        assert not engine.can_see(bob.id, obj.id)

    def test_visible_ids_deduplicates(self, mgr, lc, proj, engine, principals):
        """Object owned + projected should appear once."""
        alice, _, _ = principals
        scope = lc.create_scope(name="S", owner_id=alice.id)
        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={})
        proj.project(scope_id=scope.id, object_id=obj.id, projected_by=alice.id)

        visible = engine.visible_object_ids(alice.id)
        assert visible.count(obj.id) == 1


class TestAccessLevel:

    def test_owner_gets_admin(self, mgr, engine, principals):
        alice, _, _ = principals
        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={})
        assert engine.get_access_level(alice.id, obj.id) == AccessLevel.ADMIN

    def test_member_gets_projection_level(self, mgr, lc, proj, engine, principals):
        alice, bob, _ = principals
        scope = lc.create_scope(name="S", owner_id=alice.id)
        lc.add_member(scope.id, principal_id=bob.id, granted_by=alice.id)
        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={})
        proj.project(
            scope_id=scope.id, object_id=obj.id,
            projected_by=alice.id, access_level=AccessLevel.WRITE,
        )

        assert engine.get_access_level(bob.id, obj.id) == AccessLevel.WRITE

    def test_highest_level_wins(self, mgr, lc, proj, engine, principals):
        alice, bob, _ = principals
        s1 = lc.create_scope(name="S1", owner_id=alice.id)
        s2 = lc.create_scope(name="S2", owner_id=alice.id)
        lc.add_member(s1.id, principal_id=bob.id, granted_by=alice.id)
        lc.add_member(s2.id, principal_id=bob.id, granted_by=alice.id)

        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={})
        proj.project(scope_id=s1.id, object_id=obj.id, projected_by=alice.id, access_level=AccessLevel.READ)
        proj.project(scope_id=s2.id, object_id=obj.id, projected_by=alice.id, access_level=AccessLevel.ADMIN)

        assert engine.get_access_level(bob.id, obj.id) == AccessLevel.ADMIN

    def test_no_access_returns_none(self, mgr, engine, principals):
        alice, bob, _ = principals
        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={})
        assert engine.get_access_level(bob.id, obj.id) is None


class TestScopeHierarchy:

    def test_ancestor_scope_ids(self, lc, engine, principals):
        alice, _, _ = principals
        org = lc.create_scope(name="Org", owner_id=alice.id)
        team = lc.create_scope(name="Team", owner_id=alice.id, parent_scope_id=org.id)
        project = lc.create_scope(name="Project", owner_id=alice.id, parent_scope_id=team.id)

        ancestors = engine.ancestor_scope_ids(project.id)
        assert ancestors == [team.id, org.id]

    def test_descendant_scope_ids(self, lc, engine, principals):
        alice, _, _ = principals
        org = lc.create_scope(name="Org", owner_id=alice.id)
        team1 = lc.create_scope(name="Team1", owner_id=alice.id, parent_scope_id=org.id)
        team2 = lc.create_scope(name="Team2", owner_id=alice.id, parent_scope_id=org.id)
        proj1 = lc.create_scope(name="Proj1", owner_id=alice.id, parent_scope_id=team1.id)

        descendants = engine.descendant_scope_ids(org.id)
        assert set(descendants) == {team1.id, team2.id, proj1.id}

    def test_hierarchy_visibility(self, mgr, lc, proj, engine, principals):
        """Member of child scope can see objects projected into parent scope."""
        alice, bob, _ = principals
        parent = lc.create_scope(name="Org", owner_id=alice.id)
        child = lc.create_scope(name="Team", owner_id=alice.id, parent_scope_id=parent.id)
        lc.add_member(child.id, principal_id=bob.id, granted_by=alice.id)

        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={})
        proj.project(scope_id=parent.id, object_id=obj.id, projected_by=alice.id)

        assert engine.can_see(bob.id, obj.id)

    def test_scope_member_ids(self, lc, engine, principals):
        alice, bob, carol = principals
        scope = lc.create_scope(name="S", owner_id=alice.id)
        lc.add_member(scope.id, principal_id=bob.id, granted_by=alice.id)
        lc.add_member(scope.id, principal_id=carol.id, granted_by=alice.id)

        members = engine.scope_member_ids(scope.id)
        assert set(members) == {alice.id, bob.id, carol.id}

    def test_filter_by_object_type(self, mgr, engine, principals):
        alice, _, _ = principals
        mgr.create(object_type="Doc", owner_id=alice.id, data={})
        mgr.create(object_type="Task", owner_id=alice.id, data={})
        mgr.create(object_type="Doc", owner_id=alice.id, data={})

        docs = engine.visible_object_ids(alice.id, object_type="Doc")
        tasks = engine.visible_object_ids(alice.id, object_type="Task")
        assert len(docs) == 2
        assert len(tasks) == 1
