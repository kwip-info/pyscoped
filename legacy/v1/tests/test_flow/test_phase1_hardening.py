"""Layer 9 Phase 1 hardening — correctness and access control.

Covers the behaviors added in the Flow/Promotion hardening pass:
* ``PromotionManager.promote`` actually creates a scope projection.
* Cross-pipeline stage transitions are rejected.
* ``from_stage_id`` must match the object's actual current stage.
* Archived pipelines reject further transitions.
* Transitions require the actor to own the object.
* Missing env / scope / object / stage references raise scoped errors,
  not raw ``IntegrityError``.
"""

from __future__ import annotations

import pytest

from scoped.environments.lifecycle import EnvironmentLifecycle
from scoped.exceptions import (
    AccessDeniedError,
    EnvironmentNotFoundError,
    FlowError,
    PromotionDeniedError,
    ScopeNotFoundError,
    StageTransitionDeniedError,
)
from scoped.flow.engine import FlowEngine
from scoped.flow.pipeline import PipelineManager
from scoped.flow.promotion import PromotionManager
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.tenancy.projection import ProjectionManager


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


@pytest.fixture
def scopes(sqlite_backend):
    return ScopeLifecycle(sqlite_backend)


@pytest.fixture
def envs(sqlite_backend):
    return EnvironmentLifecycle(sqlite_backend)


@pytest.fixture
def projections(sqlite_backend):
    return ProjectionManager(sqlite_backend)


@pytest.fixture
def flow_engine(sqlite_backend):
    return FlowEngine(sqlite_backend)


@pytest.fixture
def promotions(sqlite_backend, projections):
    """PromotionManager with projection wiring but no flow channel enforcement."""
    return PromotionManager(
        sqlite_backend,
        projection_manager=projections,
    )


# ----- Promotion actually promotes ---------------------------------------

class TestPromotionProjects:

    def test_promote_creates_scope_projection(
        self, promotions, envs, scopes, objects, principals,
    ):
        alice, _ = principals
        env = envs.spawn(name="Env", owner_id=alice.id)
        envs.activate(env.id, actor_id=alice.id)
        scope = scopes.create_scope(name="Target", owner_id=alice.id)
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"t": "x"},
        )

        promotions.promote(
            object_id=obj.id,
            source_env_id=env.id,
            target_scope_id=scope.id,
            promoted_by=alice.id,
        )

        # Verify a projection was created
        pm = ProjectionManager(promotions._backend)
        visible = pm.get_projections(scope.id)
        assert any(p.object_id == obj.id for p in visible)

    def test_promote_without_projection_manager_still_inserts_row(
        self, sqlite_backend, envs, scopes, objects, principals,
    ):
        alice, _ = principals
        pm = PromotionManager(sqlite_backend)
        env = envs.spawn(name="Env", owner_id=alice.id)
        envs.activate(env.id, actor_id=alice.id)
        scope = scopes.create_scope(name="Target", owner_id=alice.id)
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"t": "x"},
        )
        promo = pm.promote(
            object_id=obj.id,
            source_env_id=env.id,
            target_scope_id=scope.id,
            promoted_by=alice.id,
        )
        assert promo.id is not None


# ----- Missing references raise scoped errors ---------------------------

class TestPromotionReferenceValidation:

    def test_missing_scope_raises_scoped_error(
        self, promotions, envs, objects, principals,
    ):
        alice, _ = principals
        env = envs.spawn(name="E", owner_id=alice.id)
        envs.activate(env.id, actor_id=alice.id)
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"t": "x"},
        )
        with pytest.raises(ScopeNotFoundError):
            promotions.promote(
                object_id=obj.id,
                source_env_id=env.id,
                target_scope_id="nope",
                promoted_by=alice.id,
            )

    def test_missing_env_raises_scoped_error(
        self, promotions, scopes, objects, principals,
    ):
        alice, _ = principals
        scope = scopes.create_scope(name="S", owner_id=alice.id)
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"t": "x"},
        )
        with pytest.raises(EnvironmentNotFoundError):
            promotions.promote(
                object_id=obj.id,
                source_env_id="nope",
                target_scope_id=scope.id,
                promoted_by=alice.id,
            )

    def test_missing_object_raises_scoped_error(
        self, promotions, envs, scopes, principals,
    ):
        alice, _ = principals
        env = envs.spawn(name="E", owner_id=alice.id)
        envs.activate(env.id, actor_id=alice.id)
        scope = scopes.create_scope(name="S", owner_id=alice.id)
        with pytest.raises(PromotionDeniedError):
            promotions.promote(
                object_id="nope",
                source_env_id=env.id,
                target_scope_id=scope.id,
                promoted_by=alice.id,
            )

    def test_missing_stage_raises_scoped_error(
        self, promotions, envs, scopes, objects, principals,
    ):
        alice, _ = principals
        env = envs.spawn(name="E", owner_id=alice.id)
        envs.activate(env.id, actor_id=alice.id)
        scope = scopes.create_scope(name="S", owner_id=alice.id)
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"t": "x"},
        )
        with pytest.raises(PromotionDeniedError):
            promotions.promote(
                object_id=obj.id,
                source_env_id=env.id,
                target_scope_id=scope.id,
                target_stage_id="nope",
                promoted_by=alice.id,
            )


# ----- Transition pipeline coherence ------------------------------------

class TestTransitionPipelineCoherence:

    def test_cross_pipeline_transition_denied(
        self, pipelines, objects, principals,
    ):
        alice, _ = principals
        p1 = pipelines.create_pipeline(name="P1", owner_id=alice.id)
        p2 = pipelines.create_pipeline(name="P2", owner_id=alice.id)
        s1 = pipelines.add_stage(p1.id, name="dev", ordinal=0)
        s2 = pipelines.add_stage(p2.id, name="alpha", ordinal=0)
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"t": "x"},
        )
        pipelines.transition(obj.id, s1.id, transitioned_by=alice.id)
        with pytest.raises(StageTransitionDeniedError, match="different pipelines"):
            pipelines.transition(
                obj.id, s2.id, transitioned_by=alice.id, from_stage_id=s1.id,
            )

    def test_archived_pipeline_rejects_transitions(
        self, pipelines, objects, principals,
    ):
        alice, _ = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        s = pipelines.add_stage(p.id, name="dev", ordinal=0)
        pipelines.archive_pipeline(p.id, archived_by=alice.id)
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"t": "x"},
        )
        with pytest.raises(StageTransitionDeniedError, match="archived"):
            pipelines.transition(obj.id, s.id, transitioned_by=alice.id)


# ----- Transition current-stage validation ------------------------------

class TestTransitionCurrentStage:

    def test_initial_placement_with_existing_stage_denied(
        self, pipelines, objects, principals,
    ):
        alice, _ = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        s0 = pipelines.add_stage(p.id, name="draft", ordinal=0)
        s1 = pipelines.add_stage(p.id, name="review", ordinal=1)
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"t": "x"},
        )
        pipelines.transition(obj.id, s0.id, transitioned_by=alice.id)
        # Second initial placement (no from_stage_id) must fail
        with pytest.raises(StageTransitionDeniedError, match="already has a current stage"):
            pipelines.transition(obj.id, s1.id, transitioned_by=alice.id)

    def test_wrong_from_stage_denied(
        self, pipelines, objects, principals,
    ):
        alice, _ = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        s0 = pipelines.add_stage(p.id, name="draft", ordinal=0)
        s1 = pipelines.add_stage(p.id, name="review", ordinal=1)
        s2 = pipelines.add_stage(p.id, name="prod", ordinal=2)
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"t": "x"},
        )
        pipelines.transition(obj.id, s0.id, transitioned_by=alice.id)
        # Claim we're at s1 when we're actually at s0
        with pytest.raises(StageTransitionDeniedError, match="current stage"):
            pipelines.transition(
                obj.id, s2.id, transitioned_by=alice.id, from_stage_id=s1.id,
            )


# ----- Transition access control ----------------------------------------

class TestTransitionAccessControl:

    def test_non_owner_denied(self, pipelines, objects, principals):
        alice, bob = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        s = pipelines.add_stage(p.id, name="dev", ordinal=0)
        obj, _ = objects.create(
            object_type="Doc", owner_id=alice.id, data={"t": "x"},
        )
        with pytest.raises(AccessDeniedError):
            pipelines.transition(obj.id, s.id, transitioned_by=bob.id)

    def test_missing_object_raises_flow_error(self, pipelines, principals):
        alice, _ = principals
        p = pipelines.create_pipeline(name="P", owner_id=alice.id)
        s = pipelines.add_stage(p.id, name="dev", ordinal=0)
        with pytest.raises(FlowError, match="Object"):
            pipelines.transition("nope", s.id, transitioned_by=alice.id)
