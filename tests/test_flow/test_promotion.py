"""Tests for promotion — moving objects from environments into scopes."""

import pytest

from scoped.environments.lifecycle import EnvironmentLifecycle
from scoped.exceptions import PromotionDeniedError
from scoped.flow.engine import FlowEngine
from scoped.flow.models import FlowPointType
from scoped.flow.pipeline import PipelineManager
from scoped.flow.promotion import PromotionManager
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.tenancy.lifecycle import ScopeLifecycle


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    return alice


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
def flow_engine(sqlite_backend):
    return FlowEngine(sqlite_backend)


@pytest.fixture
def pipelines(sqlite_backend):
    return PipelineManager(sqlite_backend)


@pytest.fixture
def promotions(sqlite_backend):
    return PromotionManager(sqlite_backend)


@pytest.fixture
def promotions_with_flow(sqlite_backend, flow_engine):
    return PromotionManager(sqlite_backend, flow_engine=flow_engine)


@pytest.fixture
def env_and_scope(envs, scopes, principals):
    """An active environment and a target scope."""
    env = envs.spawn(name="WorkEnv", owner_id=principals.id)
    envs.activate(env.id, actor_id=principals.id)
    scope = scopes.create_scope(name="Target", owner_id=principals.id)
    return env, scope


class TestPromote:

    def test_basic_promotion(self, promotions, env_and_scope, objects, principals):
        env, scope = env_and_scope
        obj, _ = objects.create(
            object_type="Doc", owner_id=principals.id, data={"title": "Result"},
        )
        promo = promotions.promote(
            object_id=obj.id,
            source_env_id=env.id,
            target_scope_id=scope.id,
            promoted_by=principals.id,
        )
        assert promo.object_id == obj.id
        assert promo.source_env_id == env.id
        assert promo.target_scope_id == scope.id
        assert promo.target_stage_id is None

    def test_promotion_with_stage(
        self, promotions, env_and_scope, objects, pipelines, principals,
    ):
        env, scope = env_and_scope
        p = pipelines.create_pipeline(name="P", owner_id=principals.id)
        draft = pipelines.add_stage(p.id, name="draft", ordinal=0)
        obj, _ = objects.create(
            object_type="Doc", owner_id=principals.id, data={"t": "x"},
        )

        promo = promotions.promote(
            object_id=obj.id,
            source_env_id=env.id,
            target_scope_id=scope.id,
            target_stage_id=draft.id,
            promoted_by=principals.id,
        )
        assert promo.target_stage_id == draft.id

    def test_promotion_with_flow_channel(
        self, promotions_with_flow, flow_engine,
        env_and_scope, objects, principals,
    ):
        env, scope = env_and_scope
        flow_engine.create_channel(
            name="Promote",
            source_type=FlowPointType.ENVIRONMENT, source_id=env.id,
            target_type=FlowPointType.SCOPE, target_id=scope.id,
            owner_id=principals.id,
        )
        obj, _ = objects.create(
            object_type="Doc", owner_id=principals.id, data={"t": "x"},
        )

        promo = promotions_with_flow.promote(
            object_id=obj.id,
            source_env_id=env.id,
            target_scope_id=scope.id,
            promoted_by=principals.id,
        )
        assert promo is not None

    def test_promotion_denied_without_channel(
        self, promotions_with_flow, env_and_scope, objects, principals,
    ):
        env, scope = env_and_scope
        obj, _ = objects.create(
            object_type="Doc", owner_id=principals.id, data={"t": "x"},
        )

        with pytest.raises(PromotionDeniedError):
            promotions_with_flow.promote(
                object_id=obj.id,
                source_env_id=env.id,
                target_scope_id=scope.id,
                promoted_by=principals.id,
            )

    def test_promotion_denied_by_type_filter(
        self, promotions_with_flow, flow_engine,
        env_and_scope, objects, principals,
    ):
        env, scope = env_and_scope
        flow_engine.create_channel(
            name="Doc only",
            source_type=FlowPointType.ENVIRONMENT, source_id=env.id,
            target_type=FlowPointType.SCOPE, target_id=scope.id,
            owner_id=principals.id,
            allowed_types=["Doc"],
        )
        obj, _ = objects.create(
            object_type="Task", owner_id=principals.id, data={"t": "x"},
        )

        with pytest.raises(PromotionDeniedError):
            promotions_with_flow.promote(
                object_id=obj.id,
                source_env_id=env.id,
                target_scope_id=scope.id,
                promoted_by=principals.id,
                object_type="Task",
            )


class TestGet:

    def test_get_existing(self, promotions, env_and_scope, objects, principals):
        env, scope = env_and_scope
        obj, _ = objects.create(
            object_type="Doc", owner_id=principals.id, data={"t": "x"},
        )
        promo = promotions.promote(
            object_id=obj.id, source_env_id=env.id,
            target_scope_id=scope.id, promoted_by=principals.id,
        )
        fetched = promotions.get(promo.id)
        assert fetched is not None
        assert fetched.id == promo.id

    def test_get_nonexistent(self, promotions):
        assert promotions.get("nonexistent") is None


class TestListPromotions:

    def test_list_by_env(self, promotions, env_and_scope, objects, principals):
        env, scope = env_and_scope
        o1, _ = objects.create(object_type="Doc", owner_id=principals.id, data={"a": 1})
        o2, _ = objects.create(object_type="Doc", owner_id=principals.id, data={"b": 2})
        promotions.promote(
            object_id=o1.id, source_env_id=env.id,
            target_scope_id=scope.id, promoted_by=principals.id,
        )
        promotions.promote(
            object_id=o2.id, source_env_id=env.id,
            target_scope_id=scope.id, promoted_by=principals.id,
        )

        result = promotions.list_promotions(source_env_id=env.id)
        assert len(result) == 2

    def test_list_by_scope(self, promotions, env_and_scope, objects, principals):
        env, scope = env_and_scope
        obj, _ = objects.create(
            object_type="Doc", owner_id=principals.id, data={"t": "x"},
        )
        promotions.promote(
            object_id=obj.id, source_env_id=env.id,
            target_scope_id=scope.id, promoted_by=principals.id,
        )
        result = promotions.list_promotions(target_scope_id=scope.id)
        assert len(result) == 1

    def test_list_by_object(self, promotions, env_and_scope, objects, principals):
        env, scope = env_and_scope
        obj, _ = objects.create(
            object_type="Doc", owner_id=principals.id, data={"t": "x"},
        )
        promotions.promote(
            object_id=obj.id, source_env_id=env.id,
            target_scope_id=scope.id, promoted_by=principals.id,
        )
        result = promotions.list_promotions(object_id=obj.id)
        assert len(result) == 1

    def test_count(self, promotions, env_and_scope, objects, principals):
        env, scope = env_and_scope
        assert promotions.count_promotions(env.id) == 0

        obj, _ = objects.create(
            object_type="Doc", owner_id=principals.id, data={"t": "x"},
        )
        promotions.promote(
            object_id=obj.id, source_env_id=env.id,
            target_scope_id=scope.id, promoted_by=principals.id,
        )
        assert promotions.count_promotions(env.id) == 1
