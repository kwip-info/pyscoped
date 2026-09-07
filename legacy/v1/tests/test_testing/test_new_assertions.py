"""Tests for 4F: New pytest assertion helpers, markers, and fixtures."""

import pytest

from scoped.exceptions import AccessDeniedError
from scoped.storage.sa_sqlite import SASQLiteBackend
from scoped.testing.assertions import (
    assert_access_denied,
    assert_can_read,
    assert_cannot_read,
    assert_trace_exists,
)


@pytest.fixture
def backend():
    b = SASQLiteBackend(":memory:")
    b.initialize()
    yield b
    b.close()


@pytest.fixture
def services(backend):
    from scoped.manifest._services import build_services
    return build_services(backend)


class TestAssertAccessDenied:
    def test_passes_when_access_denied_raised(self):
        def denied():
            raise AccessDeniedError("nope")

        assert_access_denied(denied)

    def test_fails_when_no_exception(self):
        def allowed():
            return True

        with pytest.raises(AssertionError, match="Expected AccessDeniedError"):
            assert_access_denied(allowed)

    def test_fails_when_wrong_exception(self):
        def wrong():
            raise ValueError("other")

        with pytest.raises(ValueError):
            assert_access_denied(wrong)


class TestAssertCanRead:
    def test_passes_for_owner(self, backend, services):
        p = services.principals.create_principal(
            kind="user", display_name="Alice", principal_id="alice",
        )
        obj, _ = services.manager.create(
            object_type="doc", owner_id=p.id, data={"x": 1},
        )
        assert_can_read(backend, obj.id, p.id)

    def test_fails_for_non_owner(self, backend, services):
        p = services.principals.create_principal(
            kind="user", display_name="Alice", principal_id="alice",
        )
        services.principals.create_principal(
            kind="user", display_name="Bob", principal_id="bob",
        )
        obj, _ = services.manager.create(
            object_type="doc", owner_id=p.id, data={"x": 1},
        )
        with pytest.raises(AssertionError, match="should be able to read"):
            assert_can_read(backend, obj.id, "bob")


class TestAssertCannotRead:
    def test_passes_for_non_owner(self, backend, services):
        p = services.principals.create_principal(
            kind="user", display_name="Alice", principal_id="alice",
        )
        services.principals.create_principal(
            kind="user", display_name="Bob", principal_id="bob",
        )
        obj, _ = services.manager.create(
            object_type="doc", owner_id=p.id, data={"x": 1},
        )
        assert_cannot_read(backend, obj.id, "bob")

    def test_fails_for_owner(self, backend, services):
        p = services.principals.create_principal(
            kind="user", display_name="Alice", principal_id="alice",
        )
        obj, _ = services.manager.create(
            object_type="doc", owner_id=p.id, data={"x": 1},
        )
        with pytest.raises(AssertionError, match="should NOT be able to read"):
            assert_cannot_read(backend, obj.id, p.id)


class TestAssertTraceExists:
    def test_passes_for_existing_entry(self, backend, services):
        p = services.principals.create_principal(
            kind="user", display_name="Alice", principal_id="alice",
        )
        services.manager.create(
            object_type="doc", owner_id=p.id, data={"x": 1},
        )
        assert_trace_exists(backend, actor_id="alice", action="create")

    def test_fails_for_nonexistent_entry(self, backend):
        with pytest.raises(AssertionError, match="No audit entry found"):
            assert_trace_exists(backend, actor_id="nobody", action="delete")

    def test_partial_criteria(self, backend, services):
        p = services.principals.create_principal(
            kind="user", display_name="Alice", principal_id="alice",
        )
        services.manager.create(
            object_type="doc", owner_id=p.id, data={"x": 1},
        )
        # Just actor_id
        assert_trace_exists(backend, actor_id="alice")
        # Just action
        assert_trace_exists(backend, action="create")


class TestMarkers:
    def test_markers_importable(self):
        from scoped.testing.markers import sqlite_only, postgres_only
        assert sqlite_only is not None
        assert postgres_only is not None


class TestScopedTxnFixture:
    def test_fixture_importable(self):
        from scoped.testing.fixtures import scoped_txn
        assert scoped_txn is not None
