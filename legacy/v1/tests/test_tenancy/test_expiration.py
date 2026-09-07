"""Tests for P3-3E: Membership Expiration Enforcement."""

import pytest
from datetime import timedelta

import sqlalchemy as sa

from scoped.identity.principal import PrincipalStore
from scoped.storage._query import compile_for
from scoped.storage._schema import scope_memberships
from scoped.tenancy.engine import VisibilityEngine
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.tenancy.models import ScopeMembership, ScopeRole, active_membership_condition
from scoped.types import Lifecycle, now_utc


@pytest.fixture
def principal_store(sqlite_backend, registry):
    return PrincipalStore(sqlite_backend)


@pytest.fixture
def scopes(sqlite_backend):
    return ScopeLifecycle(sqlite_backend)


@pytest.fixture
def engine(sqlite_backend):
    return VisibilityEngine(sqlite_backend)


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


# ---------------------------------------------------------------------------
# ScopeMembership.is_expired property
# ---------------------------------------------------------------------------

class TestIsExpiredProperty:

    def test_no_expiry_not_expired(self, scopes, alice, bob):
        scope = scopes.create_scope(name="Team", owner_id=alice.id)
        mem = scopes.add_member(
            scope.id, principal_id=bob.id, role=ScopeRole.VIEWER,
            granted_by=alice.id,
        )
        assert mem.is_expired is False

    def test_future_expiry_not_expired(self, scopes, alice, bob):
        future = now_utc() + timedelta(hours=1)
        scope = scopes.create_scope(name="Team", owner_id=alice.id)
        mem = scopes.add_member(
            scope.id, principal_id=bob.id, role=ScopeRole.VIEWER,
            granted_by=alice.id, expires_at=future,
        )
        assert mem.is_expired is False

    def test_past_expiry_is_expired(self, scopes, alice, bob):
        past = now_utc() - timedelta(hours=1)
        scope = scopes.create_scope(name="Team", owner_id=alice.id)
        mem = scopes.add_member(
            scope.id, principal_id=bob.id, role=ScopeRole.VIEWER,
            granted_by=alice.id, expires_at=past,
        )
        assert mem.is_expired is True


# ---------------------------------------------------------------------------
# is_member() — expiration filtering + lazy archival
# ---------------------------------------------------------------------------

class TestIsMemberExpiration:

    def test_active_no_expiry_is_member(self, scopes, alice, bob):
        """Active membership without expiry is visible."""
        scope = scopes.create_scope(name="Team", owner_id=alice.id)
        scopes.add_member(
            scope.id, principal_id=bob.id, role=ScopeRole.VIEWER,
            granted_by=alice.id,
        )
        assert scopes.is_member(scope.id, bob.id) is True

    def test_active_future_expiry_is_member(self, scopes, alice, bob):
        """Active membership with future expiry is visible."""
        future = now_utc() + timedelta(hours=1)
        scope = scopes.create_scope(name="Team", owner_id=alice.id)
        scopes.add_member(
            scope.id, principal_id=bob.id, role=ScopeRole.VIEWER,
            granted_by=alice.id, expires_at=future,
        )
        assert scopes.is_member(scope.id, bob.id) is True

    def test_expired_membership_not_member(self, scopes, alice, bob):
        """Expired membership is NOT visible."""
        past = now_utc() - timedelta(hours=1)
        scope = scopes.create_scope(name="Team", owner_id=alice.id)
        scopes.add_member(
            scope.id, principal_id=bob.id, role=ScopeRole.VIEWER,
            granted_by=alice.id, expires_at=past,
        )
        assert scopes.is_member(scope.id, bob.id) is False

    def test_expired_membership_lazily_archived(self, scopes, sqlite_backend, alice, bob):
        """Calling is_member on an expired membership archives it."""
        past = now_utc() - timedelta(hours=1)
        scope = scopes.create_scope(name="Team", owner_id=alice.id)
        scopes.add_member(
            scope.id, principal_id=bob.id, role=ScopeRole.VIEWER,
            granted_by=alice.id, expires_at=past,
        )

        # Confirm the membership is ACTIVE in the DB before the check
        stmt = sa.select(scope_memberships.c.lifecycle).where(
            scope_memberships.c.scope_id == scope.id,
            scope_memberships.c.principal_id == bob.id,
        )
        sql, params = compile_for(stmt, sqlite_backend.dialect)
        row = sqlite_backend.fetch_one(sql, params)
        assert row["lifecycle"] == "ACTIVE"

        # Trigger lazy archival via is_member
        assert scopes.is_member(scope.id, bob.id) is False

        # Confirm the membership is now ARCHIVED in the DB
        row = sqlite_backend.fetch_one(sql, params)
        assert row["lifecycle"] == "ARCHIVED"


# ---------------------------------------------------------------------------
# get_memberships() — expiration filtering
# ---------------------------------------------------------------------------

class TestGetMembershipsExpiration:

    def test_excludes_expired(self, scopes, alice, bob):
        """get_memberships excludes expired memberships."""
        past = now_utc() - timedelta(hours=1)
        scope = scopes.create_scope(name="Team", owner_id=alice.id)
        scopes.add_member(
            scope.id, principal_id=bob.id, role=ScopeRole.VIEWER,
            granted_by=alice.id, expires_at=past,
        )
        members = scopes.get_memberships(scope.id)
        # Only the owner (alice) should be present — bob's expired
        principal_ids = [m.principal_id for m in members]
        assert bob.id not in principal_ids
        assert alice.id in principal_ids

    def test_includes_non_expired(self, scopes, alice, bob):
        """get_memberships includes non-expired memberships."""
        future = now_utc() + timedelta(hours=1)
        scope = scopes.create_scope(name="Team", owner_id=alice.id)
        scopes.add_member(
            scope.id, principal_id=bob.id, role=ScopeRole.VIEWER,
            granted_by=alice.id, expires_at=future,
        )
        members = scopes.get_memberships(scope.id)
        principal_ids = [m.principal_id for m in members]
        assert bob.id in principal_ids

    def test_includes_no_expiry(self, scopes, alice, bob):
        """get_memberships includes memberships with no expiry."""
        scope = scopes.create_scope(name="Team", owner_id=alice.id)
        scopes.add_member(
            scope.id, principal_id=bob.id, role=ScopeRole.VIEWER,
            granted_by=alice.id,
        )
        members = scopes.get_memberships(scope.id)
        principal_ids = [m.principal_id for m in members]
        assert bob.id in principal_ids


# ---------------------------------------------------------------------------
# get_principal_scopes() — expiration filtering
# ---------------------------------------------------------------------------

class TestGetPrincipalScopesExpiration:

    def test_excludes_expired(self, scopes, alice, bob):
        """get_principal_scopes excludes expired memberships."""
        past = now_utc() - timedelta(hours=1)
        scope = scopes.create_scope(name="Team", owner_id=alice.id)
        scopes.add_member(
            scope.id, principal_id=bob.id, role=ScopeRole.VIEWER,
            granted_by=alice.id, expires_at=past,
        )
        bob_scopes = scopes.get_principal_scopes(bob.id)
        scope_ids = [m.scope_id for m in bob_scopes]
        assert scope.id not in scope_ids

    def test_includes_non_expired(self, scopes, alice, bob):
        """get_principal_scopes includes non-expired memberships."""
        future = now_utc() + timedelta(hours=1)
        scope = scopes.create_scope(name="Team", owner_id=alice.id)
        scopes.add_member(
            scope.id, principal_id=bob.id, role=ScopeRole.VIEWER,
            granted_by=alice.id, expires_at=future,
        )
        bob_scopes = scopes.get_principal_scopes(bob.id)
        scope_ids = [m.scope_id for m in bob_scopes]
        assert scope.id in scope_ids


# ---------------------------------------------------------------------------
# VisibilityEngine — expiration filtering
# ---------------------------------------------------------------------------

class TestVisibilityEngineExpiration:

    def test_scope_member_ids_excludes_expired(self, scopes, engine, alice, bob):
        """scope_member_ids excludes expired memberships."""
        past = now_utc() - timedelta(hours=1)
        scope = scopes.create_scope(name="Team", owner_id=alice.id)
        scopes.add_member(
            scope.id, principal_id=bob.id, role=ScopeRole.VIEWER,
            granted_by=alice.id, expires_at=past,
        )
        member_ids = engine.scope_member_ids(scope.id)
        assert bob.id not in member_ids
        assert alice.id in member_ids

    def test_scope_member_ids_includes_non_expired(self, scopes, engine, alice, bob):
        """scope_member_ids includes non-expired memberships."""
        future = now_utc() + timedelta(hours=1)
        scope = scopes.create_scope(name="Team", owner_id=alice.id)
        scopes.add_member(
            scope.id, principal_id=bob.id, role=ScopeRole.VIEWER,
            granted_by=alice.id, expires_at=future,
        )
        member_ids = engine.scope_member_ids(scope.id)
        assert bob.id in member_ids


# ---------------------------------------------------------------------------
# active_membership_condition helper
# ---------------------------------------------------------------------------

class TestActiveMembershipCondition:

    def test_condition_filters_expired(self, scopes, sqlite_backend, alice, bob):
        """active_membership_condition excludes expired rows."""
        past = now_utc() - timedelta(hours=1)
        scope = scopes.create_scope(name="Team", owner_id=alice.id)
        scopes.add_member(
            scope.id, principal_id=bob.id, role=ScopeRole.VIEWER,
            granted_by=alice.id, expires_at=past,
        )

        stmt = sa.select(scope_memberships).where(
            scope_memberships.c.scope_id == scope.id,
            active_membership_condition(scope_memberships),
        )
        sql, params = compile_for(stmt, sqlite_backend.dialect)
        rows = sqlite_backend.fetch_all(sql, params)
        principal_ids = [r["principal_id"] for r in rows]
        assert bob.id not in principal_ids
        # Owner (alice) has no expiry so should be included
        assert alice.id in principal_ids

    def test_condition_includes_null_expiry(self, scopes, sqlite_backend, alice, bob):
        """active_membership_condition includes rows with NULL expires_at."""
        scope = scopes.create_scope(name="Team", owner_id=alice.id)
        scopes.add_member(
            scope.id, principal_id=bob.id, role=ScopeRole.VIEWER,
            granted_by=alice.id,
        )

        stmt = sa.select(scope_memberships).where(
            scope_memberships.c.scope_id == scope.id,
            active_membership_condition(scope_memberships),
        )
        sql, params = compile_for(stmt, sqlite_backend.dialect)
        rows = sqlite_backend.fetch_all(sql, params)
        principal_ids = [r["principal_id"] for r in rows]
        assert bob.id in principal_ids
        assert alice.id in principal_ids
