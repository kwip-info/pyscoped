"""Tests for AuditQuery — filtered reads and chain verification."""

import pytest

from scoped.audit.query import AuditQuery, ChainVerification
from scoped.audit.writer import AuditWriter
from scoped.types import ActionType


@pytest.fixture
def writer(sqlite_backend):
    return AuditWriter(sqlite_backend)


@pytest.fixture
def query(sqlite_backend):
    return AuditQuery(sqlite_backend)


@pytest.fixture
def populated(writer):
    """Create a set of trace entries for query tests."""
    writer.record(actor_id="alice", action=ActionType.CREATE, target_type="Doc", target_id="d1", scope_id="s1")
    writer.record(actor_id="alice", action=ActionType.UPDATE, target_type="Doc", target_id="d1", scope_id="s1")
    writer.record(actor_id="bob", action=ActionType.READ, target_type="Doc", target_id="d1", scope_id="s1")
    writer.record(actor_id="alice", action=ActionType.CREATE, target_type="Task", target_id="t1", scope_id="s2")
    writer.record(actor_id="bob", action=ActionType.DELETE, target_type="Doc", target_id="d1", scope_id="s1")
    return writer


class TestAuditQueryLookups:

    def test_get_by_id(self, populated, query):
        entries = query.query(limit=1)
        found = query.get(entries[0].id)
        assert found is not None
        assert found.sequence == 1

    def test_get_by_sequence(self, populated, query):
        found = query.get_by_sequence(3)
        assert found is not None
        assert found.actor_id == "bob"
        assert found.action == ActionType.READ

    def test_get_missing_returns_none(self, query):
        assert query.get("nonexistent") is None
        assert query.get_by_sequence(999) is None


class TestAuditQueryFilters:

    def test_filter_by_actor(self, populated, query):
        results = query.query(actor_id="alice")
        assert len(results) == 3
        assert all(e.actor_id == "alice" for e in results)

    def test_filter_by_action(self, populated, query):
        results = query.query(action=ActionType.CREATE)
        assert len(results) == 2

    def test_filter_by_target_type(self, populated, query):
        results = query.query(target_type="Doc")
        assert len(results) == 4

    def test_filter_by_target_id(self, populated, query):
        results = query.query(target_id="t1")
        assert len(results) == 1
        assert results[0].target_type == "Task"

    def test_filter_by_scope(self, populated, query):
        results = query.query(scope_id="s2")
        assert len(results) == 1

    def test_combined_filters(self, populated, query):
        results = query.query(actor_id="alice", action=ActionType.CREATE)
        assert len(results) == 2

    def test_limit_and_offset(self, populated, query):
        page1 = query.query(limit=2, offset=0)
        page2 = query.query(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0].sequence == 1
        assert page2[0].sequence == 3

    def test_count(self, populated, query):
        assert query.count() == 5
        assert query.count(actor_id="alice") == 3
        assert query.count(action=ActionType.DELETE) == 1

    def test_history(self, populated, query):
        history = query.history("Doc", "d1")
        assert len(history) == 4
        actions = [e.action for e in history]
        assert ActionType.CREATE in actions
        assert ActionType.DELETE in actions


class TestAuditQueryNested:

    def test_children(self, writer, query):
        parent = writer.record(
            actor_id="alice", action=ActionType.PROMOTION,
            target_type="Doc", target_id="d1",
        )
        writer.record(
            actor_id="alice", action=ActionType.PROJECTION,
            target_type="Doc", target_id="d1",
            parent_trace_id=parent.id,
        )
        writer.record(
            actor_id="alice", action=ActionType.SCOPE_MODIFY,
            target_type="Scope", target_id="s1",
            parent_trace_id=parent.id,
        )
        # Unrelated entry
        writer.record(
            actor_id="bob", action=ActionType.READ,
            target_type="Doc", target_id="d2",
        )

        children = query.children(parent.id)
        assert len(children) == 2
        assert all(c.parent_trace_id == parent.id for c in children)


class TestChainVerification:

    def test_valid_chain(self, writer, query):
        for i in range(5):
            writer.record(
                actor_id="u1", action=ActionType.CREATE,
                target_type="X", target_id=f"x{i}",
            )
        result = query.verify_chain()
        assert result.valid
        assert result.entries_checked == 5
        assert bool(result) is True

    def test_empty_chain(self, query):
        result = query.verify_chain()
        assert result.valid
        assert result.entries_checked == 0

    def test_tampered_hash_detected(self, sqlite_backend, writer, query):
        writer.record(actor_id="u1", action=ActionType.CREATE, target_type="X", target_id="x1")
        writer.record(actor_id="u1", action=ActionType.CREATE, target_type="X", target_id="x2")
        writer.record(actor_id="u1", action=ActionType.CREATE, target_type="X", target_id="x3")

        # Tamper with entry 2's hash
        sqlite_backend.execute(
            "UPDATE audit_trail SET hash = 'tampered' WHERE sequence = 2"
        )

        result = query.verify_chain()
        assert not result.valid
        assert result.broken_at_sequence == 2
        assert repr(result).startswith("ChainVerification(valid=False")

    def test_broken_chain_link_detected(self, sqlite_backend, writer, query):
        writer.record(actor_id="u1", action=ActionType.CREATE, target_type="X", target_id="x1")
        writer.record(actor_id="u1", action=ActionType.CREATE, target_type="X", target_id="x2")
        writer.record(actor_id="u1", action=ActionType.CREATE, target_type="X", target_id="x3")

        # Break the chain link on entry 3
        sqlite_backend.execute(
            "UPDATE audit_trail SET previous_hash = 'wrong' WHERE sequence = 3"
        )

        result = query.verify_chain()
        assert not result.valid
        assert result.broken_at_sequence == 3

    def test_partial_range_verification(self, writer, query):
        for i in range(10):
            writer.record(
                actor_id="u1", action=ActionType.CREATE,
                target_type="X", target_id=f"x{i}",
            )
        result = query.verify_chain(from_sequence=3, to_sequence=7)
        assert result.valid
        assert result.entries_checked == 5
        assert result.first_sequence == 3
        assert result.last_sequence == 7

    def test_chain_verification_repr(self, writer, query):
        writer.record(actor_id="u1", action=ActionType.CREATE, target_type="X", target_id="x1")
        result = query.verify_chain()
        assert "valid=True" in repr(result)
