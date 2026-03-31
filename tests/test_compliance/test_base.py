"""Tests for ScopedTestCase base class."""

from __future__ import annotations

import pytest

from scoped.exceptions import AccessDeniedError
from scoped.testing.base import ScopedTestCase


class TestScopedTestCaseSetup:
    def test_setup_creates_backend(self, sqlite_backend):
        tc = ScopedTestCase()
        tc.setup_scoped(sqlite_backend)

        assert tc.backend is sqlite_backend
        assert tc.audit is not None
        assert tc.manager is not None
        assert tc.principals is not None

    def test_setup_creates_principals(self, sqlite_backend):
        tc = ScopedTestCase()
        tc.setup_scoped(sqlite_backend)

        assert tc.user_a is not None
        assert tc.user_b is not None
        assert tc.user_c is not None
        assert tc.user_a.id != tc.user_b.id

    def test_setup_without_backend_creates_default(self):
        tc = ScopedTestCase()
        tc.setup_scoped()

        assert tc.backend is not None
        assert tc.user_a is not None


class TestScopedTestCaseHelpers:
    def test_create_object(self, sqlite_backend):
        tc = ScopedTestCase()
        tc.setup_scoped(sqlite_backend)

        obj = tc.create_object("Document", owner=tc.user_a)

        assert obj is not None
        assert obj.owner_id == tc.user_a.id

    def test_read_object_by_owner(self, sqlite_backend):
        tc = ScopedTestCase()
        tc.setup_scoped(sqlite_backend)

        obj = tc.create_object("Document", owner=tc.user_a)
        result = tc.read_object(obj.id, as_principal=tc.user_a)

        assert result is not None
        assert result.id == obj.id

    def test_read_object_by_non_owner_returns_none(self, sqlite_backend):
        tc = ScopedTestCase()
        tc.setup_scoped(sqlite_backend)

        obj = tc.create_object("Document", owner=tc.user_a)
        result = tc.read_object(obj.id, as_principal=tc.user_b)

        assert result is None

    def test_create_scope(self, sqlite_backend):
        tc = ScopedTestCase()
        tc.setup_scoped(sqlite_backend)

        scope = tc.create_scope(owner=tc.user_a)

        assert scope is not None
        assert scope.owner_id == tc.user_a.id

    def test_create_scope_with_members(self, sqlite_backend):
        tc = ScopedTestCase()
        tc.setup_scoped(sqlite_backend)

        scope = tc.create_scope(owner=tc.user_a, members=[tc.user_b])

        assert scope is not None

    def test_as_principal_context(self, sqlite_backend):
        from scoped.identity.context import ScopedContext

        tc = ScopedTestCase()
        tc.setup_scoped(sqlite_backend)

        with tc.as_principal(tc.user_a) as ctx:
            assert ctx.principal_id == tc.user_a.id
            assert ScopedContext.current().principal_id == tc.user_a.id


class TestScopedTestCaseAssertions:
    def test_assert_access_denied_with_none(self, sqlite_backend):
        tc = ScopedTestCase()
        tc.setup_scoped(sqlite_backend)

        obj = tc.create_object("Document", owner=tc.user_a)

        # user_b cannot read user_a's object
        tc.assert_access_denied(
            lambda: tc.read_object(obj.id, as_principal=tc.user_b)
        )

    def test_assert_access_denied_fails_when_allowed(self, sqlite_backend):
        tc = ScopedTestCase()
        tc.setup_scoped(sqlite_backend)

        obj = tc.create_object("Document", owner=tc.user_a)

        with pytest.raises(AssertionError, match="Expected access to be denied"):
            tc.assert_access_denied(
                lambda: tc.read_object(obj.id, as_principal=tc.user_a)
            )

    def test_assert_can_read(self, sqlite_backend):
        tc = ScopedTestCase()
        tc.setup_scoped(sqlite_backend)

        obj = tc.create_object("Document", owner=tc.user_a)
        result = tc.assert_can_read(obj.id, as_principal=tc.user_a)

        assert result.id == obj.id

    def test_assert_cannot_read(self, sqlite_backend):
        tc = ScopedTestCase()
        tc.setup_scoped(sqlite_backend)

        obj = tc.create_object("Document", owner=tc.user_a)
        tc.assert_cannot_read(obj.id, as_principal=tc.user_b)

    def test_assert_cannot_read_fails_when_readable(self, sqlite_backend):
        tc = ScopedTestCase()
        tc.setup_scoped(sqlite_backend)

        obj = tc.create_object("Document", owner=tc.user_a)

        with pytest.raises(AssertionError, match="NOT be able to read"):
            tc.assert_cannot_read(obj.id, as_principal=tc.user_a)

    def test_assert_trace_exists(self, sqlite_backend):
        tc = ScopedTestCase()
        tc.setup_scoped(sqlite_backend)

        obj = tc.create_object("Document", owner=tc.user_a)

        tc.assert_trace_exists(
            actor_id=tc.user_a.id,
            action="create",
            target_id=obj.id,
        )

    def test_assert_trace_exists_fails_when_missing(self, sqlite_backend):
        tc = ScopedTestCase()
        tc.setup_scoped(sqlite_backend)

        with pytest.raises(AssertionError, match="Expected trace entry"):
            tc.assert_trace_exists(
                actor_id="nonexistent",
                action="create",
                target_id="nonexistent",
            )

    def test_assert_version_count(self, sqlite_backend):
        tc = ScopedTestCase()
        tc.setup_scoped(sqlite_backend)

        obj = tc.create_object("Document", owner=tc.user_a)
        tc.assert_version_count(obj.id, 1, as_principal=tc.user_a)

    def test_assert_version_count_after_update(self, sqlite_backend):
        tc = ScopedTestCase()
        tc.setup_scoped(sqlite_backend)

        obj = tc.create_object("Document", owner=tc.user_a, data={"v": 1})
        tc.manager.update(obj.id, principal_id=tc.user_a.id, data={"v": 2})

        tc.assert_version_count(obj.id, 2, as_principal=tc.user_a)
