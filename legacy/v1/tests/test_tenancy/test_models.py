"""Tests for tenancy data models."""

from scoped.tenancy.models import (
    AccessLevel,
    Scope,
    ScopeMembership,
    ScopeProjection,
    ScopeRole,
)
from scoped.types import Lifecycle, now_utc


class TestScope:

    def test_snapshot(self):
        ts = now_utc()
        scope = Scope(
            id="s1", name="Team A", owner_id="u1",
            created_at=ts, description="A team scope",
            metadata={"region": "us"},
        )
        snap = scope.snapshot()
        assert snap["id"] == "s1"
        assert snap["name"] == "Team A"
        assert snap["description"] == "A team scope"
        assert snap["metadata"] == {"region": "us"}
        assert snap["lifecycle"] == "ACTIVE"

    def test_is_active(self):
        s = Scope(id="s", name="S", owner_id="u", created_at=now_utc())
        assert s.is_active
        assert not s.is_frozen
        assert not s.is_archived

    def test_is_frozen(self):
        s = Scope(
            id="s", name="S", owner_id="u", created_at=now_utc(),
            lifecycle=Lifecycle.DEPRECATED,
        )
        assert s.is_frozen
        assert not s.is_active

    def test_is_archived(self):
        s = Scope(
            id="s", name="S", owner_id="u", created_at=now_utc(),
            lifecycle=Lifecycle.ARCHIVED,
        )
        assert s.is_archived
        assert not s.is_active


class TestScopeMembership:

    def test_snapshot(self):
        ts = now_utc()
        mem = ScopeMembership(
            id="m1", scope_id="s1", principal_id="u1",
            role=ScopeRole.EDITOR, granted_at=ts, granted_by="admin",
        )
        snap = mem.snapshot()
        assert snap["role"] == "editor"
        assert snap["scope_id"] == "s1"

    def test_is_active(self):
        mem = ScopeMembership(
            id="m1", scope_id="s1", principal_id="u1",
            role=ScopeRole.VIEWER, granted_at=now_utc(), granted_by="u",
        )
        assert mem.is_active


class TestScopeProjection:

    def test_snapshot(self):
        ts = now_utc()
        proj = ScopeProjection(
            id="p1", scope_id="s1", object_id="obj1",
            projected_at=ts, projected_by="u1",
            access_level=AccessLevel.WRITE,
        )
        snap = proj.snapshot()
        assert snap["access_level"] == "write"
        assert snap["scope_id"] == "s1"


class TestEnums:

    def test_scope_roles(self):
        assert ScopeRole.VIEWER.value == "viewer"
        assert ScopeRole.OWNER.value == "owner"

    def test_access_levels(self):
        assert AccessLevel.READ.value == "read"
        assert AccessLevel.ADMIN.value == "admin"
