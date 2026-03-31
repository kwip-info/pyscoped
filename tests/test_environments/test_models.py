"""Tests for environment data models."""

from scoped.environments.models import (
    Environment,
    EnvironmentObject,
    EnvironmentSnapshot,
    EnvironmentState,
    EnvironmentTemplate,
    ObjectOrigin,
    VALID_TRANSITIONS,
    compute_snapshot_checksum,
)
from scoped.types import Lifecycle, now_utc


class TestEnvironment:

    def test_snapshot(self):
        ts = now_utc()
        env = Environment(
            id="e1", name="Test Env", owner_id="alice",
            created_at=ts, state=EnvironmentState.ACTIVE,
            scope_id="s1", ephemeral=True,
        )
        snap = env.snapshot()
        assert snap["id"] == "e1"
        assert snap["state"] == "active"
        assert snap["ephemeral"] is True
        assert snap["scope_id"] == "s1"

    def test_is_active(self):
        env = Environment(
            id="e1", name="E", owner_id="u", created_at=now_utc(),
            state=EnvironmentState.ACTIVE,
        )
        assert env.is_active

    def test_is_not_active(self):
        env = Environment(
            id="e1", name="E", owner_id="u", created_at=now_utc(),
            state=EnvironmentState.SPAWNING,
        )
        assert not env.is_active

    def test_is_terminal(self):
        env = Environment(
            id="e1", name="E", owner_id="u", created_at=now_utc(),
            state=EnvironmentState.DISCARDED,
        )
        assert env.is_terminal

    def test_is_mutable(self):
        active = Environment(
            id="e1", name="E", owner_id="u", created_at=now_utc(),
            state=EnvironmentState.ACTIVE,
        )
        suspended = Environment(
            id="e2", name="E", owner_id="u", created_at=now_utc(),
            state=EnvironmentState.SUSPENDED,
        )
        assert active.is_mutable
        assert not suspended.is_mutable

    def test_can_transition_to(self):
        env = Environment(
            id="e1", name="E", owner_id="u", created_at=now_utc(),
            state=EnvironmentState.ACTIVE,
        )
        assert env.can_transition_to(EnvironmentState.SUSPENDED)
        assert env.can_transition_to(EnvironmentState.COMPLETED)
        assert not env.can_transition_to(EnvironmentState.DISCARDED)
        assert not env.can_transition_to(EnvironmentState.SPAWNING)


class TestEnvironmentTemplate:

    def test_snapshot(self):
        ts = now_utc()
        tmpl = EnvironmentTemplate(
            id="t1", name="Code Review", owner_id="alice",
            created_at=ts, config={"rules": ["read_only"]},
        )
        snap = tmpl.snapshot()
        assert snap["name"] == "Code Review"
        assert snap["config"] == {"rules": ["read_only"]}

    def test_is_active(self):
        tmpl = EnvironmentTemplate(
            id="t1", name="T", owner_id="u", created_at=now_utc(),
        )
        assert tmpl.is_active


class TestEnvironmentSnapshot:

    def test_snapshot(self):
        ts = now_utc()
        snap = EnvironmentSnapshot(
            id="s1", environment_id="e1", name="Checkpoint 1",
            snapshot_data={"objects": []}, created_at=ts,
            created_by="alice", checksum="abc",
        )
        s = snap.snapshot()
        assert s["environment_id"] == "e1"
        assert s["checksum"] == "abc"


class TestEnvironmentObject:

    def test_snapshot(self):
        ts = now_utc()
        eo = EnvironmentObject(
            id="eo1", environment_id="e1", object_id="obj1",
            origin=ObjectOrigin.CREATED, added_at=ts,
        )
        snap = eo.snapshot()
        assert snap["origin"] == "created"
        assert snap["object_id"] == "obj1"


class TestEnums:

    def test_environment_states(self):
        assert EnvironmentState.SPAWNING.value == "spawning"
        assert EnvironmentState.ACTIVE.value == "active"
        assert EnvironmentState.SUSPENDED.value == "suspended"
        assert EnvironmentState.COMPLETED.value == "completed"
        assert EnvironmentState.DISCARDED.value == "discarded"
        assert EnvironmentState.PROMOTED.value == "promoted"

    def test_object_origins(self):
        assert ObjectOrigin.CREATED.value == "created"
        assert ObjectOrigin.PROJECTED.value == "projected"


class TestValidTransitions:

    def test_spawning_to_active(self):
        assert EnvironmentState.ACTIVE in VALID_TRANSITIONS[EnvironmentState.SPAWNING]

    def test_active_to_suspended_and_completed(self):
        transitions = VALID_TRANSITIONS[EnvironmentState.ACTIVE]
        assert EnvironmentState.SUSPENDED in transitions
        assert EnvironmentState.COMPLETED in transitions

    def test_discarded_is_terminal(self):
        assert len(VALID_TRANSITIONS[EnvironmentState.DISCARDED]) == 0

    def test_promoted_can_discard(self):
        assert EnvironmentState.DISCARDED in VALID_TRANSITIONS[EnvironmentState.PROMOTED]


class TestChecksum:

    def test_deterministic(self):
        data = {"a": 1, "b": [2, 3]}
        assert compute_snapshot_checksum(data) == compute_snapshot_checksum(data)

    def test_key_order_irrelevant(self):
        d1 = {"b": 2, "a": 1}
        d2 = {"a": 1, "b": 2}
        assert compute_snapshot_checksum(d1) == compute_snapshot_checksum(d2)

    def test_different_data_different_checksum(self):
        assert compute_snapshot_checksum({"a": 1}) != compute_snapshot_checksum({"a": 2})
