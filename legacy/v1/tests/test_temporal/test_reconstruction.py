"""Tests for point-in-time state reconstruction."""

import time
from datetime import timedelta

import pytest

from scoped.audit.writer import AuditWriter
from scoped.identity.principal import PrincipalStore
from scoped.temporal.reconstruction import ReconstructedState, StateReconstructor
from scoped.types import ActionType, now_utc


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    return store.create_principal(kind="user", display_name="Alice", principal_id="alice")


@pytest.fixture
def writer(sqlite_backend):
    return AuditWriter(sqlite_backend)


@pytest.fixture
def reconstructor(sqlite_backend):
    return StateReconstructor(sqlite_backend)


class TestReconstruct:

    def test_no_traces(self, reconstructor):
        result = reconstructor.reconstruct("object", "nonexistent", now_utc())
        assert not result.found
        assert result.state is None
        assert result.trace_id is None

    def test_single_trace(self, writer, reconstructor, principals):
        writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
            after_state={"name": "Doc", "version": 1},
        )
        result = reconstructor.reconstruct("object", "obj1", now_utc())
        assert result.found
        assert result.state == {"name": "Doc", "version": 1}
        assert result.trace_id is not None

    def test_multiple_traces_returns_latest_before_timestamp(
        self, writer, reconstructor, principals,
    ):
        t1 = now_utc()
        writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
            after_state={"version": 1},
        )
        t2 = now_utc()
        writer.record(
            actor_id="alice", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
            before_state={"version": 1},
            after_state={"version": 2},
        )
        t3 = now_utc()
        writer.record(
            actor_id="alice", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
            before_state={"version": 2},
            after_state={"version": 3},
        )

        # Reconstruct at t3 should show version 3
        result = reconstructor.reconstruct("object", "obj1", now_utc())
        assert result.state == {"version": 3}

    def test_reconstruct_before_any_trace(self, writer, reconstructor, principals):
        past = now_utc() - timedelta(hours=1)
        writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
            after_state={"version": 1},
        )
        result = reconstructor.reconstruct("object", "obj1", past)
        assert not result.found

    def test_reconstruct_ignores_other_targets(self, writer, reconstructor, principals):
        writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
            after_state={"name": "Doc1"},
        )
        writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="object", target_id="obj2",
            after_state={"name": "Doc2"},
        )
        result = reconstructor.reconstruct("object", "obj1", now_utc())
        assert result.state == {"name": "Doc1"}

    def test_reconstruct_ignores_null_after_state(self, writer, reconstructor, principals):
        writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
            after_state={"version": 1},
        )
        writer.record(
            actor_id="alice", action=ActionType.DELETE,
            target_type="object", target_id="obj1",
            before_state={"version": 1},
            # No after_state — object is gone
        )
        # Should still find the create's after_state
        result = reconstructor.reconstruct("object", "obj1", now_utc())
        assert result.found
        assert result.state == {"version": 1}

    def test_works_for_non_object_targets(self, writer, reconstructor, principals):
        writer.record(
            actor_id="alice", action=ActionType.SCOPE_CREATE,
            target_type="scope", target_id="s1",
            after_state={"name": "Team scope", "lifecycle": "ACTIVE"},
        )
        result = reconstructor.reconstruct("scope", "s1", now_utc())
        assert result.found
        assert result.state["name"] == "Team scope"

    def test_snapshot(self, writer, reconstructor, principals):
        writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
            after_state={"v": 1},
        )
        result = reconstructor.reconstruct("object", "obj1", now_utc())
        snap = result.snapshot()
        assert snap["found"] is True
        assert snap["target_type"] == "object"
        assert snap["target_id"] == "obj1"
        assert snap["state"] == {"v": 1}


class TestReconstructMany:

    def test_multiple_targets(self, writer, reconstructor, principals):
        writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
            after_state={"name": "A"},
        )
        writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="scope", target_id="s1",
            after_state={"name": "B"},
        )
        results = reconstructor.reconstruct_many(
            [("object", "obj1"), ("scope", "s1"), ("object", "missing")],
            now_utc(),
        )
        assert len(results) == 3
        assert results[0].found
        assert results[1].found
        assert not results[2].found


class TestHistoryAt:

    def test_timeline(self, writer, reconstructor, principals):
        t_before = now_utc()
        writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
            after_state={"v": 1},
        )
        t_after_create = now_utc()
        writer.record(
            actor_id="alice", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
            after_state={"v": 2},
        )
        t_after_update = now_utc()

        results = reconstructor.history_at(
            "object", "obj1",
            [t_before, t_after_create, t_after_update],
        )
        assert len(results) == 3
        assert not results[0].found
        assert results[1].state == {"v": 1}
        assert results[2].state == {"v": 2}
