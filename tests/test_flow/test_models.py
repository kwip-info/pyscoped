"""Tests for flow data models."""

from scoped.flow.models import (
    FlowChannel,
    FlowPointType,
    Pipeline,
    Promotion,
    Stage,
    StageTransition,
)
from scoped.types import Lifecycle, now_utc


class TestPipeline:

    def test_snapshot(self):
        ts = now_utc()
        p = Pipeline(
            id="p1", name="Code Review", owner_id="alice",
            description="Review pipeline", created_at=ts,
        )
        snap = p.snapshot()
        assert snap["id"] == "p1"
        assert snap["name"] == "Code Review"
        assert snap["lifecycle"] == "ACTIVE"

    def test_is_active(self):
        p = Pipeline(id="p", name="P", owner_id="u", created_at=now_utc())
        assert p.is_active
        p.lifecycle = Lifecycle.ARCHIVED
        assert not p.is_active


class TestStage:

    def test_snapshot(self):
        s = Stage(id="s1", pipeline_id="p1", name="draft", ordinal=0)
        snap = s.snapshot()
        assert snap["name"] == "draft"
        assert snap["ordinal"] == 0
        assert snap["pipeline_id"] == "p1"

    def test_metadata(self):
        s = Stage(
            id="s1", pipeline_id="p1", name="review",
            ordinal=1, metadata={"approvers": 2},
        )
        assert s.metadata["approvers"] == 2


class TestStageTransition:

    def test_snapshot(self):
        ts = now_utc()
        t = StageTransition(
            id="t1", object_id="obj1",
            from_stage_id="s1", to_stage_id="s2",
            transitioned_at=ts, transitioned_by="alice",
            reason="approved",
        )
        snap = t.snapshot()
        assert snap["from_stage_id"] == "s1"
        assert snap["to_stage_id"] == "s2"
        assert snap["reason"] == "approved"

    def test_initial_placement(self):
        ts = now_utc()
        t = StageTransition(
            id="t1", object_id="obj1",
            to_stage_id="s1",
            transitioned_at=ts, transitioned_by="alice",
        )
        assert t.from_stage_id is None


class TestFlowChannel:

    def test_snapshot(self):
        ts = now_utc()
        ch = FlowChannel(
            id="ch1", name="Env to Scope",
            source_type=FlowPointType.ENVIRONMENT, source_id="e1",
            target_type=FlowPointType.SCOPE, target_id="s1",
            owner_id="alice", created_at=ts,
            allowed_types=["Doc", "Task"],
        )
        snap = ch.snapshot()
        assert snap["source_type"] == "environment"
        assert snap["target_type"] == "scope"
        assert snap["allowed_types"] == ["Doc", "Task"]

    def test_allows_type_empty(self):
        ch = FlowChannel(
            id="ch1", name="C",
            source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id="u", created_at=now_utc(),
        )
        assert ch.allows_type("anything")

    def test_allows_type_restricted(self):
        ch = FlowChannel(
            id="ch1", name="C",
            source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id="u", created_at=now_utc(),
            allowed_types=["Doc"],
        )
        assert ch.allows_type("Doc")
        assert not ch.allows_type("Task")

    def test_is_active(self):
        ch = FlowChannel(
            id="ch1", name="C",
            source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id="u", created_at=now_utc(),
        )
        assert ch.is_active


class TestPromotion:

    def test_snapshot(self):
        ts = now_utc()
        p = Promotion(
            id="pr1", object_id="obj1",
            source_env_id="e1", target_scope_id="s1",
            target_stage_id="st1",
            promoted_at=ts, promoted_by="alice",
        )
        snap = p.snapshot()
        assert snap["object_id"] == "obj1"
        assert snap["source_env_id"] == "e1"
        assert snap["target_stage_id"] == "st1"

    def test_no_target_stage(self):
        ts = now_utc()
        p = Promotion(
            id="pr1", object_id="obj1",
            source_env_id="e1", target_scope_id="s1",
            promoted_at=ts, promoted_by="alice",
        )
        assert p.target_stage_id is None


class TestEnums:

    def test_flow_point_types(self):
        assert FlowPointType.ENVIRONMENT.value == "environment"
        assert FlowPointType.SCOPE.value == "scope"
        assert FlowPointType.STAGE.value == "stage"
