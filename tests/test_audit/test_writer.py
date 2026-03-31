"""Tests for AuditWriter — append-only writer with hash chaining."""

import pytest

from scoped.audit.writer import AuditWriter
from scoped.types import ActionType


class TestAuditWriter:

    @pytest.fixture
    def writer(self, sqlite_backend):
        return AuditWriter(sqlite_backend)

    def test_record_creates_entry(self, writer):
        entry = writer.record(
            actor_id="user-1",
            action=ActionType.CREATE,
            target_type="Document",
            target_id="doc-1",
        )
        assert entry.sequence == 1
        assert entry.actor_id == "user-1"
        assert entry.action == ActionType.CREATE
        assert entry.previous_hash == ""
        assert entry.hash != ""

    def test_sequence_increments(self, writer):
        e1 = writer.record(
            actor_id="u1", action=ActionType.CREATE,
            target_type="X", target_id="x1",
        )
        e2 = writer.record(
            actor_id="u1", action=ActionType.UPDATE,
            target_type="X", target_id="x1",
        )
        assert e2.sequence == e1.sequence + 1

    def test_hash_chain(self, writer):
        e1 = writer.record(
            actor_id="u1", action=ActionType.CREATE,
            target_type="X", target_id="x1",
        )
        e2 = writer.record(
            actor_id="u1", action=ActionType.UPDATE,
            target_type="X", target_id="x1",
        )
        assert e2.previous_hash == e1.hash
        assert e1.previous_hash == ""

    def test_before_and_after_state(self, writer):
        entry = writer.record(
            actor_id="u1",
            action=ActionType.UPDATE,
            target_type="Document",
            target_id="doc-1",
            before_state={"title": "Old"},
            after_state={"title": "New"},
        )
        assert entry.before_state == {"title": "Old"}
        assert entry.after_state == {"title": "New"}

    def test_scope_id(self, writer):
        entry = writer.record(
            actor_id="u1",
            action=ActionType.CREATE,
            target_type="Document",
            target_id="doc-1",
            scope_id="scope-42",
        )
        assert entry.scope_id == "scope-42"

    def test_metadata(self, writer):
        entry = writer.record(
            actor_id="u1",
            action=ActionType.CREATE,
            target_type="Document",
            target_id="doc-1",
            metadata={"ip": "10.0.0.1", "user_agent": "test"},
        )
        assert entry.metadata["ip"] == "10.0.0.1"

    def test_parent_trace_id(self, writer):
        parent = writer.record(
            actor_id="u1", action=ActionType.PROMOTION,
            target_type="Document", target_id="doc-1",
        )
        child = writer.record(
            actor_id="u1", action=ActionType.PROJECTION,
            target_type="Document", target_id="doc-1",
            parent_trace_id=parent.id,
        )
        assert child.parent_trace_id == parent.id

    def test_last_sequence_and_hash(self, writer):
        assert writer.last_sequence == 0
        assert writer.last_hash == ""

        e = writer.record(
            actor_id="u1", action=ActionType.CREATE,
            target_type="X", target_id="x",
        )
        assert writer.last_sequence == 1
        assert writer.last_hash == e.hash

    def test_record_batch(self, writer):
        entries = writer.record_batch([
            {"actor_id": "u1", "action": ActionType.CREATE, "target_type": "A", "target_id": "a1"},
            {"actor_id": "u1", "action": ActionType.CREATE, "target_type": "B", "target_id": "b1"},
            {"actor_id": "u1", "action": ActionType.CREATE, "target_type": "C", "target_id": "c1"},
        ])
        assert len(entries) == 3
        assert entries[0].sequence == 1
        assert entries[1].sequence == 2
        assert entries[2].sequence == 3
        # Chain links
        assert entries[1].previous_hash == entries[0].hash
        assert entries[2].previous_hash == entries[1].hash

    def test_seeds_from_existing_data(self, sqlite_backend):
        """A new writer picks up where the last one left off."""
        w1 = AuditWriter(sqlite_backend)
        e = w1.record(
            actor_id="u1", action=ActionType.CREATE,
            target_type="X", target_id="x",
        )

        w2 = AuditWriter(sqlite_backend)
        assert w2.last_sequence == 1
        assert w2.last_hash == e.hash

        e2 = w2.record(
            actor_id="u1", action=ActionType.UPDATE,
            target_type="X", target_id="x",
        )
        assert e2.sequence == 2
        assert e2.previous_hash == e.hash

    def test_persists_to_database(self, sqlite_backend, writer):
        writer.record(
            actor_id="u1", action=ActionType.CREATE,
            target_type="X", target_id="x",
        )
        row = sqlite_backend.fetch_one(
            "SELECT * FROM audit_trail WHERE sequence = 1"
        )
        assert row is not None
        assert row["actor_id"] == "u1"
        assert row["action"] == "create"
