"""Tests for TraceEntry and hash computation."""

from scoped.audit.models import TraceEntry, compute_hash
from scoped.types import ActionType, generate_id, now_utc


class TestComputeHash:

    def test_deterministic(self):
        kwargs = dict(
            entry_id="abc",
            sequence=1,
            actor_id="user-1",
            action="create",
            target_type="Document",
            target_id="doc-1",
            timestamp="2026-01-01T00:00:00+00:00",
            previous_hash="",
        )
        h1 = compute_hash(**kwargs)
        h2 = compute_hash(**kwargs)
        assert h1 == h2

    def test_different_inputs_different_hash(self):
        base = dict(
            entry_id="abc",
            sequence=1,
            actor_id="user-1",
            action="create",
            target_type="Document",
            target_id="doc-1",
            timestamp="2026-01-01T00:00:00+00:00",
            previous_hash="",
        )
        h1 = compute_hash(**base)
        h2 = compute_hash(**{**base, "actor_id": "user-2"})
        assert h1 != h2

    def test_previous_hash_affects_output(self):
        base = dict(
            entry_id="abc",
            sequence=1,
            actor_id="user-1",
            action="create",
            target_type="Document",
            target_id="doc-1",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        h1 = compute_hash(**base, previous_hash="")
        h2 = compute_hash(**base, previous_hash="deadbeef")
        assert h1 != h2

    def test_sha256_output_length(self):
        h = compute_hash(
            entry_id="x", sequence=1, actor_id="a", action="create",
            target_type="T", target_id="t",
            timestamp="2026-01-01T00:00:00+00:00", previous_hash="",
        )
        assert len(h) == 64  # SHA-256 hex


class TestTraceEntry:

    def test_snapshot(self):
        ts = now_utc()
        entry = TraceEntry(
            id="e1",
            sequence=1,
            actor_id="user-1",
            action=ActionType.CREATE,
            target_type="Document",
            target_id="doc-1",
            timestamp=ts,
            hash="abc123",
            previous_hash="",
            before_state=None,
            after_state={"title": "Hello"},
        )
        snap = entry.snapshot()
        assert snap["id"] == "e1"
        assert snap["action"] == "create"
        assert snap["after_state"] == {"title": "Hello"}
        assert snap["before_state"] is None
        assert snap["hash"] == "abc123"

    def test_snapshot_with_metadata(self):
        ts = now_utc()
        entry = TraceEntry(
            id="e2",
            sequence=2,
            actor_id="user-1",
            action=ActionType.UPDATE,
            target_type="Document",
            target_id="doc-1",
            timestamp=ts,
            hash="def456",
            metadata={"ip": "127.0.0.1"},
        )
        snap = entry.snapshot()
        assert snap["metadata"]["ip"] == "127.0.0.1"
