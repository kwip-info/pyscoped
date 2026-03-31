"""Tests for pipeline and stage management."""

import pytest

from scoped.exceptions import FlowError
from scoped.flow.pipeline import PipelineManager
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    bob = store.create_principal(kind="user", display_name="Bob", principal_id="bob")
    return alice, bob


@pytest.fixture
def pipelines(sqlite_backend):
    return PipelineManager(sqlite_backend)


@pytest.fixture
def objects(sqlite_backend):
    return ScopedManager(sqlite_backend)


class TestPipelineCRUD:

    def test_create(self, pipelines, principals):
        alice, _ = principals
        p = pipelines.create_pipeline(
            name="Code Review", owner_id=alice.id, description="Review flow",
        )
        assert p.name == "Code Review"
        assert p.is_active

    def test_get(self, pipelines, principals):
        alice, _ = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        fetched = pipelines.get_pipeline(p.id)
        assert fetched is not None
        assert fetched.id == p.id

    def test_get_nonexistent(self, pipelines):
        assert pipelines.get_pipeline("nonexistent") is None

    def test_list(self, pipelines, principals):
        alice, _ = principals
        pipelines.create_pipeline(name="P1", owner_id=alice.id)
        pipelines.create_pipeline(name="P2", owner_id=alice.id)
        result = pipelines.list_pipelines()
        assert len(result) == 2

    def test_list_by_owner(self, pipelines, principals):
        alice, bob = principals
        pipelines.create_pipeline(name="P1", owner_id=alice.id)
        pipelines.create_pipeline(name="P2", owner_id=bob.id)
        result = pipelines.list_pipelines(owner_id=alice.id)
        assert len(result) == 1

    def test_archive(self, pipelines, principals):
        alice, _ = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        pipelines.archive_pipeline(p.id, archived_by=alice.id)
        # Should not appear in active-only list
        result = pipelines.list_pipelines()
        assert len(result) == 0


class TestStages:

    def test_add_stage(self, pipelines, principals):
        alice, _ = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        s = pipelines.add_stage(p.id, name="draft", ordinal=0)
        assert s.name == "draft"
        assert s.pipeline_id == p.id
        assert s.ordinal == 0

    def test_get_stage(self, pipelines, principals):
        alice, _ = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        s = pipelines.add_stage(p.id, name="draft", ordinal=0)
        fetched = pipelines.get_stage(s.id)
        assert fetched is not None
        assert fetched.name == "draft"

    def test_get_stages_ordered(self, pipelines, principals):
        alice, _ = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        pipelines.add_stage(p.id, name="approved", ordinal=2)
        pipelines.add_stage(p.id, name="draft", ordinal=0)
        pipelines.add_stage(p.id, name="review", ordinal=1)

        stages = pipelines.get_stages(p.id)
        assert len(stages) == 3
        assert [s.name for s in stages] == ["draft", "review", "approved"]

    def test_get_stage_by_name(self, pipelines, principals):
        alice, _ = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        pipelines.add_stage(p.id, name="draft", ordinal=0)
        pipelines.add_stage(p.id, name="review", ordinal=1)

        s = pipelines.get_stage_by_name(p.id, "review")
        assert s is not None
        assert s.name == "review"

    def test_get_stage_by_name_nonexistent(self, pipelines, principals):
        alice, _ = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        assert pipelines.get_stage_by_name(p.id, "nonexistent") is None

    def test_stage_with_metadata(self, pipelines, principals):
        alice, _ = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        s = pipelines.add_stage(
            p.id, name="review", ordinal=1,
            metadata={"min_approvers": 2},
        )
        fetched = pipelines.get_stage(s.id)
        assert fetched.metadata == {"min_approvers": 2}


class TestTransitions:

    def test_initial_placement(self, pipelines, objects, principals):
        alice, _ = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        draft = pipelines.add_stage(p.id, name="draft", ordinal=0)
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"title": "Test"},
        )

        trans = pipelines.transition(
            obj.id, draft.id, transitioned_by=alice.id,
        )
        assert trans.from_stage_id is None
        assert trans.to_stage_id == draft.id
        assert trans.object_id == obj.id

    def test_stage_to_stage(self, pipelines, objects, principals):
        alice, _ = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        draft = pipelines.add_stage(p.id, name="draft", ordinal=0)
        review = pipelines.add_stage(p.id, name="review", ordinal=1)
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"title": "T"},
        )

        pipelines.transition(obj.id, draft.id, transitioned_by=alice.id)
        trans = pipelines.transition(
            obj.id, review.id, transitioned_by=alice.id,
            from_stage_id=draft.id, reason="ready for review",
        )
        assert trans.from_stage_id == draft.id
        assert trans.to_stage_id == review.id
        assert trans.reason == "ready for review"

    def test_transition_to_nonexistent_stage(self, pipelines, objects, principals):
        alice, _ = principals
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"t": "x"},
        )
        with pytest.raises(FlowError, match="not found"):
            pipelines.transition(obj.id, "nonexistent", transitioned_by=alice.id)

    def test_transition_from_nonexistent_stage(self, pipelines, objects, principals):
        alice, _ = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        draft = pipelines.add_stage(p.id, name="draft", ordinal=0)
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"t": "x"},
        )
        with pytest.raises(FlowError, match="not found"):
            pipelines.transition(
                obj.id, draft.id, transitioned_by=alice.id,
                from_stage_id="nonexistent",
            )

    def test_get_current_stage(self, pipelines, objects, principals):
        alice, _ = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        draft = pipelines.add_stage(p.id, name="draft", ordinal=0)
        review = pipelines.add_stage(p.id, name="review", ordinal=1)
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"t": "x"},
        )

        pipelines.transition(obj.id, draft.id, transitioned_by=alice.id)
        pipelines.transition(
            obj.id, review.id, transitioned_by=alice.id,
            from_stage_id=draft.id,
        )

        current = pipelines.get_current_stage(obj.id)
        assert current is not None
        assert current.to_stage_id == review.id

    def test_get_current_stage_none(self, pipelines):
        assert pipelines.get_current_stage("nonexistent") is None

    def test_transition_history(self, pipelines, objects, principals):
        alice, _ = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        s0 = pipelines.add_stage(p.id, name="draft", ordinal=0)
        s1 = pipelines.add_stage(p.id, name="review", ordinal=1)
        s2 = pipelines.add_stage(p.id, name="approved", ordinal=2)
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"t": "x"},
        )

        pipelines.transition(obj.id, s0.id, transitioned_by=alice.id)
        pipelines.transition(
            obj.id, s1.id, transitioned_by=alice.id, from_stage_id=s0.id,
        )
        pipelines.transition(
            obj.id, s2.id, transitioned_by=alice.id, from_stage_id=s1.id,
        )

        history = pipelines.get_transition_history(obj.id)
        assert len(history) == 3
        # Newest first
        assert history[0].to_stage_id == s2.id
        assert history[2].to_stage_id == s0.id
