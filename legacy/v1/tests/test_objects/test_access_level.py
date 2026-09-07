"""Tests for projection access-level enforcement in ScopedManager.

P3 item 5F: When a visibility_engine is wired into the manager, non-owner
operations are gated by the projection's AccessLevel.  Owner access is
unconditional (ownership trumps projection level).
"""

import pytest

from scoped.exceptions import AccessDeniedError
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.tenancy.engine import VisibilityEngine
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.tenancy.models import AccessLevel, ScopeRole
from scoped.tenancy.projection import ProjectionManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
def scope_lifecycle(sqlite_backend):
    return ScopeLifecycle(sqlite_backend)


@pytest.fixture
def projection_manager(sqlite_backend):
    return ProjectionManager(sqlite_backend)


@pytest.fixture
def visibility_engine(sqlite_backend):
    return VisibilityEngine(sqlite_backend)


@pytest.fixture
def manager_with_visibility(sqlite_backend, visibility_engine):
    """ScopedManager with visibility_engine wired in."""
    return ScopedManager(
        sqlite_backend,
        visibility_engine=visibility_engine,
    )


@pytest.fixture
def manager_without_visibility(sqlite_backend):
    """ScopedManager WITHOUT visibility_engine (backward compat)."""
    return ScopedManager(sqlite_backend)


def _setup_scope_and_projection(
    *,
    scope_lifecycle,
    projection_manager,
    owner,
    member,
    obj,
    access_level,
):
    """Helper: create scope, add member, project object with given access level."""
    scope = scope_lifecycle.create_scope(name="shared", owner_id=owner.id)
    scope_lifecycle.add_member(
        scope.id,
        principal_id=member.id,
        role=ScopeRole.EDITOR,
        granted_by=owner.id,
    )
    projection_manager.project(
        scope_id=scope.id,
        object_id=obj.id,
        projected_by=owner.id,
        access_level=access_level,
    )
    return scope


# ---------------------------------------------------------------------------
# Owner access — always granted regardless of projection level
# ---------------------------------------------------------------------------

class TestOwnerBypassesAccessLevel:

    def test_owner_can_update_regardless_of_projection(
        self, manager_with_visibility, scope_lifecycle, projection_manager,
        alice, bob,
    ):
        """Owner can always update, even when projection is READ-only."""
        obj, _ = manager_with_visibility.create(
            object_type="Doc", owner_id=alice.id, data={"v": 1},
        )
        _setup_scope_and_projection(
            scope_lifecycle=scope_lifecycle,
            projection_manager=projection_manager,
            owner=alice,
            member=bob,
            obj=obj,
            access_level=AccessLevel.READ,
        )

        updated, ver = manager_with_visibility.update(
            obj.id, principal_id=alice.id, data={"v": 2},
        )
        assert updated.current_version == 2
        assert ver.data == {"v": 2}

    def test_owner_can_tombstone_regardless_of_projection(
        self, manager_with_visibility, scope_lifecycle, projection_manager,
        alice, bob,
    ):
        """Owner can always tombstone, even when projection is READ-only."""
        obj, _ = manager_with_visibility.create(
            object_type="Doc", owner_id=alice.id, data={"v": 1},
        )
        _setup_scope_and_projection(
            scope_lifecycle=scope_lifecycle,
            projection_manager=projection_manager,
            owner=alice,
            member=bob,
            obj=obj,
            access_level=AccessLevel.READ,
        )

        tomb = manager_with_visibility.tombstone(
            obj.id, principal_id=alice.id, reason="cleanup",
        )
        assert tomb.object_id == obj.id


# ---------------------------------------------------------------------------
# Non-owner: WRITE projection
# ---------------------------------------------------------------------------

class TestNonOwnerWriteProjection:

    def test_non_owner_with_write_can_update(
        self, manager_with_visibility, scope_lifecycle, projection_manager,
        alice, bob,
    ):
        """Non-owner with WRITE projection can update the object."""
        obj, _ = manager_with_visibility.create(
            object_type="Doc", owner_id=alice.id, data={"v": 1},
        )
        _setup_scope_and_projection(
            scope_lifecycle=scope_lifecycle,
            projection_manager=projection_manager,
            owner=alice,
            member=bob,
            obj=obj,
            access_level=AccessLevel.WRITE,
        )

        # Bob is not the owner, so get_or_raise would normally block him.
        # We need to verify that _check_access_level itself works correctly.
        # Call the internal check directly since get_or_raise enforces ownership.
        manager_with_visibility._check_access_level(
            obj.id, bob.id, required="write",
        )
        # Should not raise — WRITE satisfies "write" requirement

    def test_non_owner_with_write_cannot_tombstone(
        self, manager_with_visibility, scope_lifecycle, projection_manager,
        alice, bob,
    ):
        """Non-owner with WRITE projection cannot tombstone (requires ADMIN)."""
        obj, _ = manager_with_visibility.create(
            object_type="Doc", owner_id=alice.id, data={"v": 1},
        )
        _setup_scope_and_projection(
            scope_lifecycle=scope_lifecycle,
            projection_manager=projection_manager,
            owner=alice,
            member=bob,
            obj=obj,
            access_level=AccessLevel.WRITE,
        )

        with pytest.raises(AccessDeniedError, match="admin is required"):
            manager_with_visibility._check_access_level(
                obj.id, bob.id, required="admin",
            )


# ---------------------------------------------------------------------------
# Non-owner: READ projection
# ---------------------------------------------------------------------------

class TestNonOwnerReadProjection:

    def test_non_owner_with_read_cannot_update(
        self, manager_with_visibility, scope_lifecycle, projection_manager,
        alice, bob,
    ):
        """Non-owner with READ projection cannot update (requires WRITE)."""
        obj, _ = manager_with_visibility.create(
            object_type="Doc", owner_id=alice.id, data={"v": 1},
        )
        _setup_scope_and_projection(
            scope_lifecycle=scope_lifecycle,
            projection_manager=projection_manager,
            owner=alice,
            member=bob,
            obj=obj,
            access_level=AccessLevel.READ,
        )

        with pytest.raises(AccessDeniedError, match="write is required"):
            manager_with_visibility._check_access_level(
                obj.id, bob.id, required="write",
            )

    def test_non_owner_with_read_can_read(
        self, manager_with_visibility, scope_lifecycle, projection_manager,
        alice, bob,
    ):
        """Non-owner with READ projection satisfies 'read' requirement."""
        obj, _ = manager_with_visibility.create(
            object_type="Doc", owner_id=alice.id, data={"v": 1},
        )
        _setup_scope_and_projection(
            scope_lifecycle=scope_lifecycle,
            projection_manager=projection_manager,
            owner=alice,
            member=bob,
            obj=obj,
            access_level=AccessLevel.READ,
        )

        # Should not raise
        manager_with_visibility._check_access_level(
            obj.id, bob.id, required="read",
        )


# ---------------------------------------------------------------------------
# Non-owner: ADMIN projection
# ---------------------------------------------------------------------------

class TestNonOwnerAdminProjection:

    def test_non_owner_with_admin_can_tombstone(
        self, manager_with_visibility, scope_lifecycle, projection_manager,
        alice, bob,
    ):
        """Non-owner with ADMIN projection can perform admin-level operations."""
        obj, _ = manager_with_visibility.create(
            object_type="Doc", owner_id=alice.id, data={"v": 1},
        )
        _setup_scope_and_projection(
            scope_lifecycle=scope_lifecycle,
            projection_manager=projection_manager,
            owner=alice,
            member=bob,
            obj=obj,
            access_level=AccessLevel.ADMIN,
        )

        # Should not raise — ADMIN satisfies "admin" requirement
        manager_with_visibility._check_access_level(
            obj.id, bob.id, required="admin",
        )

    def test_non_owner_with_admin_can_update(
        self, manager_with_visibility, scope_lifecycle, projection_manager,
        alice, bob,
    ):
        """Non-owner with ADMIN projection can also update (ADMIN >= WRITE)."""
        obj, _ = manager_with_visibility.create(
            object_type="Doc", owner_id=alice.id, data={"v": 1},
        )
        _setup_scope_and_projection(
            scope_lifecycle=scope_lifecycle,
            projection_manager=projection_manager,
            owner=alice,
            member=bob,
            obj=obj,
            access_level=AccessLevel.ADMIN,
        )

        manager_with_visibility._check_access_level(
            obj.id, bob.id, required="write",
        )


# ---------------------------------------------------------------------------
# No projection at all
# ---------------------------------------------------------------------------

class TestNoProjection:

    def test_non_owner_without_projection_denied(
        self, manager_with_visibility, alice, bob,
    ):
        """Non-owner with no projection gets denied (level is None)."""
        obj, _ = manager_with_visibility.create(
            object_type="Doc", owner_id=alice.id, data={"v": 1},
        )

        with pytest.raises(AccessDeniedError, match="no access"):
            manager_with_visibility._check_access_level(
                obj.id, bob.id, required="read",
            )


# ---------------------------------------------------------------------------
# Backward compatibility — no visibility_engine
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:

    def test_without_visibility_engine_all_operations_allowed(
        self, manager_without_visibility, alice, bob,
    ):
        """Without visibility_engine, _check_access_level is a no-op."""
        obj, _ = manager_without_visibility.create(
            object_type="Doc", owner_id=alice.id, data={"v": 1},
        )

        # All of these should pass without raising
        manager_without_visibility._check_access_level(
            obj.id, bob.id, required="read",
        )
        manager_without_visibility._check_access_level(
            obj.id, bob.id, required="write",
        )
        manager_without_visibility._check_access_level(
            obj.id, bob.id, required="admin",
        )


# ---------------------------------------------------------------------------
# Error context
# ---------------------------------------------------------------------------

class TestErrorContext:

    def test_access_denied_carries_structured_context(
        self, manager_with_visibility, scope_lifecycle, projection_manager,
        alice, bob,
    ):
        """AccessDeniedError includes object_id, principal_id, required and actual levels."""
        obj, _ = manager_with_visibility.create(
            object_type="Doc", owner_id=alice.id, data={"v": 1},
        )
        _setup_scope_and_projection(
            scope_lifecycle=scope_lifecycle,
            projection_manager=projection_manager,
            owner=alice,
            member=bob,
            obj=obj,
            access_level=AccessLevel.READ,
        )

        with pytest.raises(AccessDeniedError) as exc_info:
            manager_with_visibility._check_access_level(
                obj.id, bob.id, required="write",
            )

        ctx = exc_info.value.context
        assert ctx["object_id"] == obj.id
        assert ctx["principal_id"] == bob.id
        assert ctx["required_level"] == "write"
        assert ctx["actual_level"] == "read"
