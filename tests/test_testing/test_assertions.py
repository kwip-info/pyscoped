"""Tests for domain-specific assertion helpers."""

from __future__ import annotations

import pytest

from scoped.audit.writer import AuditWriter
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.storage.sa_sqlite import SASQLiteBackend as SQLiteBackend
from scoped.testing.assertions import (
    assert_audit_recorded,
    assert_hash_chain_valid,
    assert_isolated,
    assert_tombstoned,
    assert_version_count,
    assert_visible,
)
from scoped.types import ActionType


@pytest.fixture
def backend():
    b = SQLiteBackend(":memory:")
    b.initialize()
    return b


@pytest.fixture
def setup(backend, registry):
    ps = PrincipalStore(backend)
    audit = AuditWriter(backend)
    mgr = ScopedManager(backend, audit_writer=audit)
    alice = ps.create_principal(kind="user", display_name="Alice", principal_id="alice-a")
    bob = ps.create_principal(kind="user", display_name="Bob", principal_id="bob-a")
    return {"backend": backend, "mgr": mgr, "audit": audit, "alice": alice, "bob": bob}


class TestAssertIsolated:
    def test_passes_when_isolated(self, setup):
        mgr, alice, bob = setup["mgr"], setup["alice"], setup["bob"]
        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={"x": 1})
        assert_isolated(setup["backend"], obj.id, alice.id, bob.id)

    def test_fails_when_visible(self, setup):
        mgr, alice = setup["mgr"], setup["alice"]
        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={"x": 1})
        with pytest.raises(AssertionError, match="should NOT"):
            assert_isolated(setup["backend"], obj.id, alice.id, alice.id)


class TestAssertVisible:
    def test_passes_when_visible(self, setup):
        mgr, alice = setup["mgr"], setup["alice"]
        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={"x": 1})
        assert_visible(setup["backend"], obj.id, alice.id)

    def test_fails_when_not_visible(self, setup):
        mgr, alice, bob = setup["mgr"], setup["alice"], setup["bob"]
        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={"x": 1})
        with pytest.raises(AssertionError, match="should be able"):
            assert_visible(setup["backend"], obj.id, bob.id)


class TestAssertAuditRecorded:
    def test_passes_when_recorded(self, setup):
        audit, alice = setup["audit"], setup["alice"]
        audit.record(
            actor_id=alice.id,
            action=ActionType.CREATE,
            target_type="test",
            target_id="t-1",
        )
        assert_audit_recorded(
            setup["backend"],
            actor_id=alice.id,
            action="CREATE",
            target_id="t-1",
        )

    def test_fails_when_not_recorded(self, setup):
        with pytest.raises(AssertionError, match="No audit entry"):
            assert_audit_recorded(
                setup["backend"],
                actor_id="nobody",
                action="DELETE",
                target_id="nope",
            )


class TestAssertVersionCount:
    def test_passes_with_correct_count(self, setup):
        mgr, alice = setup["mgr"], setup["alice"]
        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={"v": 1})
        mgr.update(obj.id, principal_id=alice.id, data={"v": 2})
        assert_version_count(setup["backend"], obj.id, 2)

    def test_fails_with_wrong_count(self, setup):
        mgr, alice = setup["mgr"], setup["alice"]
        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={"v": 1})
        with pytest.raises(AssertionError, match="expected 5"):
            assert_version_count(setup["backend"], obj.id, 5)


class TestAssertHashChainValid:
    def test_passes_with_valid_chain(self, setup):
        audit, alice = setup["audit"], setup["alice"]
        for i in range(3):
            audit.record(
                actor_id=alice.id,
                action=ActionType.CREATE,
                target_type="test",
                target_id=f"t-{i}",
            )
        assert_hash_chain_valid(setup["backend"])


class TestAssertTombstoned:
    def test_passes_when_tombstoned(self, setup):
        mgr, alice = setup["mgr"], setup["alice"]
        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={"x": 1})
        mgr.tombstone(obj.id, principal_id=alice.id, reason="test")
        assert_tombstoned(setup["backend"], obj.id)

    def test_fails_when_not_tombstoned(self, setup):
        mgr, alice = setup["mgr"], setup["alice"]
        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={"x": 1})
        with pytest.raises(AssertionError, match="not tombstoned"):
            assert_tombstoned(setup["backend"], obj.id)
