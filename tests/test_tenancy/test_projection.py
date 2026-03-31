"""Tests for ProjectionManager — projecting objects into scopes."""

import pytest

from scoped.exceptions import AccessDeniedError, ScopeFrozenError, ScopeNotFoundError
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.tenancy.models import AccessLevel
from scoped.tenancy.projection import ProjectionManager


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    bob = store.create_principal(kind="user", display_name="Bob", principal_id="bob")
    return alice, bob


@pytest.fixture
def scopes(sqlite_backend, principals):
    alice, bob = principals
    lc = ScopeLifecycle(sqlite_backend)
    return lc


@pytest.fixture
def objects(sqlite_backend):
    return ScopedManager(sqlite_backend)


@pytest.fixture
def projections(sqlite_backend):
    return ProjectionManager(sqlite_backend)


class TestProject:

    def test_project_object(self, scopes, objects, projections, principals):
        alice, _ = principals
        scope = scopes.create_scope(name="S", owner_id=alice.id)
        obj, _ = objects.create(object_type="Doc", owner_id=alice.id, data={"x": 1})

        proj = projections.project(
            scope_id=scope.id, object_id=obj.id, projected_by=alice.id,
        )
        assert proj.scope_id == scope.id
        assert proj.object_id == obj.id
        assert proj.access_level == AccessLevel.READ
        assert proj.is_active

    def test_project_with_write_access(self, scopes, objects, projections, principals):
        alice, _ = principals
        scope = scopes.create_scope(name="S", owner_id=alice.id)
        obj, _ = objects.create(object_type="Doc", owner_id=alice.id, data={})

        proj = projections.project(
            scope_id=scope.id, object_id=obj.id,
            projected_by=alice.id, access_level=AccessLevel.WRITE,
        )
        assert proj.access_level == AccessLevel.WRITE

    def test_non_owner_cannot_project(self, scopes, objects, projections, principals):
        alice, bob = principals
        scope = scopes.create_scope(name="S", owner_id=alice.id)
        obj, _ = objects.create(object_type="Doc", owner_id=alice.id, data={})

        with pytest.raises(AccessDeniedError, match="owner"):
            projections.project(
                scope_id=scope.id, object_id=obj.id, projected_by=bob.id,
            )

    def test_project_into_nonexistent_scope_raises(self, objects, projections, principals):
        alice, _ = principals
        obj, _ = objects.create(object_type="Doc", owner_id=alice.id, data={})

        with pytest.raises(ScopeNotFoundError):
            projections.project(
                scope_id="nonexistent", object_id=obj.id, projected_by=alice.id,
            )

    def test_project_nonexistent_object_raises(self, scopes, projections, principals):
        alice, _ = principals
        scope = scopes.create_scope(name="S", owner_id=alice.id)

        with pytest.raises(AccessDeniedError):
            projections.project(
                scope_id=scope.id, object_id="nonexistent", projected_by=alice.id,
            )

    def test_project_into_frozen_scope_raises(self, scopes, objects, projections, principals):
        alice, _ = principals
        scope = scopes.create_scope(name="S", owner_id=alice.id)
        obj, _ = objects.create(object_type="Doc", owner_id=alice.id, data={})
        scopes.freeze_scope(scope.id, frozen_by=alice.id)

        with pytest.raises(ScopeFrozenError):
            projections.project(
                scope_id=scope.id, object_id=obj.id, projected_by=alice.id,
            )


class TestRevokeProjection:

    def test_revoke(self, scopes, objects, projections, principals):
        alice, _ = principals
        scope = scopes.create_scope(name="S", owner_id=alice.id)
        obj, _ = objects.create(object_type="Doc", owner_id=alice.id, data={})
        projections.project(scope_id=scope.id, object_id=obj.id, projected_by=alice.id)

        assert projections.revoke_projection(
            scope_id=scope.id, object_id=obj.id, revoked_by=alice.id,
        )
        assert not projections.is_projected(scope.id, obj.id)

    def test_revoke_nonexistent_returns_false(self, scopes, projections, principals):
        alice, _ = principals
        scope = scopes.create_scope(name="S", owner_id=alice.id)
        assert not projections.revoke_projection(
            scope_id=scope.id, object_id="nonexistent", revoked_by=alice.id,
        )


class TestQueryProjections:

    def test_get_projections(self, scopes, objects, projections, principals):
        alice, _ = principals
        scope = scopes.create_scope(name="S", owner_id=alice.id)
        o1, _ = objects.create(object_type="Doc", owner_id=alice.id, data={})
        o2, _ = objects.create(object_type="Doc", owner_id=alice.id, data={})
        projections.project(scope_id=scope.id, object_id=o1.id, projected_by=alice.id)
        projections.project(scope_id=scope.id, object_id=o2.id, projected_by=alice.id)

        projs = projections.get_projections(scope.id)
        assert len(projs) == 2

    def test_get_object_projections(self, scopes, objects, projections, principals):
        alice, _ = principals
        s1 = scopes.create_scope(name="S1", owner_id=alice.id)
        s2 = scopes.create_scope(name="S2", owner_id=alice.id)
        obj, _ = objects.create(object_type="Doc", owner_id=alice.id, data={})
        projections.project(scope_id=s1.id, object_id=obj.id, projected_by=alice.id)
        projections.project(scope_id=s2.id, object_id=obj.id, projected_by=alice.id)

        projs = projections.get_object_projections(obj.id)
        assert len(projs) == 2

    def test_is_projected(self, scopes, objects, projections, principals):
        alice, _ = principals
        scope = scopes.create_scope(name="S", owner_id=alice.id)
        obj, _ = objects.create(object_type="Doc", owner_id=alice.id, data={})

        assert not projections.is_projected(scope.id, obj.id)
        projections.project(scope_id=scope.id, object_id=obj.id, projected_by=alice.id)
        assert projections.is_projected(scope.id, obj.id)

    def test_archived_projections_excluded_by_default(self, scopes, objects, projections, principals):
        alice, _ = principals
        scope = scopes.create_scope(name="S", owner_id=alice.id)
        obj, _ = objects.create(object_type="Doc", owner_id=alice.id, data={})
        projections.project(scope_id=scope.id, object_id=obj.id, projected_by=alice.id)
        projections.revoke_projection(scope_id=scope.id, object_id=obj.id, revoked_by=alice.id)

        assert len(projections.get_projections(scope.id, active_only=True)) == 0
        assert len(projections.get_projections(scope.id, active_only=False)) == 1
