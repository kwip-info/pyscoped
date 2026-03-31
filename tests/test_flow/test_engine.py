"""Tests for flow resolution engine."""

import pytest

from scoped.exceptions import FlowBlockedError
from scoped.flow.engine import FlowEngine, FlowResolution
from scoped.flow.models import FlowPointType
from scoped.identity.principal import PrincipalStore


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    return store.create_principal(kind="user", display_name="Alice", principal_id="alice")


@pytest.fixture
def engine(sqlite_backend):
    return FlowEngine(sqlite_backend)


class TestChannelCRUD:

    def test_create_channel(self, engine, principals):
        ch = engine.create_channel(
            name="Env to Scope",
            source_type=FlowPointType.ENVIRONMENT, source_id="e1",
            target_type=FlowPointType.SCOPE, target_id="s1",
            owner_id=principals.id,
        )
        assert ch.name == "Env to Scope"
        assert ch.source_type == FlowPointType.ENVIRONMENT
        assert ch.is_active

    def test_create_with_allowed_types(self, engine, principals):
        ch = engine.create_channel(
            name="Doc Channel",
            source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id=principals.id,
            allowed_types=["Doc", "Report"],
        )
        assert ch.allowed_types == ["Doc", "Report"]

    def test_get_channel(self, engine, principals):
        ch = engine.create_channel(
            name="C", source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id=principals.id,
        )
        fetched = engine.get_channel(ch.id)
        assert fetched is not None
        assert fetched.id == ch.id

    def test_get_nonexistent(self, engine):
        assert engine.get_channel("nonexistent") is None

    def test_list_channels(self, engine, principals):
        engine.create_channel(
            name="C1", source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id=principals.id,
        )
        engine.create_channel(
            name="C2", source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s3",
            owner_id=principals.id,
        )
        result = engine.list_channels()
        assert len(result) == 2

    def test_list_by_source(self, engine, principals):
        engine.create_channel(
            name="C1", source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id=principals.id,
        )
        engine.create_channel(
            name="C2", source_type=FlowPointType.ENVIRONMENT, source_id="e1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id=principals.id,
        )
        result = engine.list_channels(
            source_type=FlowPointType.SCOPE, source_id="s1",
        )
        assert len(result) == 1

    def test_list_by_target(self, engine, principals):
        engine.create_channel(
            name="C1", source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id=principals.id,
        )
        result = engine.list_channels(
            target_type=FlowPointType.SCOPE, target_id="s2",
        )
        assert len(result) == 1

    def test_archive_channel(self, engine, principals):
        ch = engine.create_channel(
            name="C", source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id=principals.id,
        )
        engine.archive_channel(ch.id)
        result = engine.list_channels(active_only=True)
        assert len(result) == 0


class TestCanFlow:

    def test_no_channel_denies(self, engine):
        result = engine.can_flow(
            source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
        )
        assert not result.allowed
        assert "No active flow channel" in result.reason

    def test_matching_channel_allows(self, engine, principals):
        engine.create_channel(
            name="C", source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id=principals.id,
        )
        result = engine.can_flow(
            source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
        )
        assert result.allowed
        assert result.channel is not None

    def test_type_filter_allows(self, engine, principals):
        engine.create_channel(
            name="C", source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id=principals.id,
            allowed_types=["Doc"],
        )
        result = engine.can_flow(
            source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            object_type="Doc",
        )
        assert result.allowed

    def test_type_filter_denies(self, engine, principals):
        engine.create_channel(
            name="C", source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id=principals.id,
            allowed_types=["Doc"],
        )
        result = engine.can_flow(
            source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            object_type="Task",
        )
        assert not result.allowed
        assert "Task" in result.reason

    def test_no_type_check_without_object_type(self, engine, principals):
        engine.create_channel(
            name="C", source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id=principals.id,
            allowed_types=["Doc"],
        )
        result = engine.can_flow(
            source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
        )
        assert result.allowed

    def test_archived_channel_not_used(self, engine, principals):
        ch = engine.create_channel(
            name="C", source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id=principals.id,
        )
        engine.archive_channel(ch.id)
        result = engine.can_flow(
            source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
        )
        assert not result.allowed


class TestCanFlowOrRaise:

    def test_raises_when_blocked(self, engine):
        with pytest.raises(FlowBlockedError):
            engine.can_flow_or_raise(
                source_type=FlowPointType.SCOPE, source_id="s1",
                target_type=FlowPointType.SCOPE, target_id="s2",
            )

    def test_returns_when_allowed(self, engine, principals):
        engine.create_channel(
            name="C", source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id=principals.id,
        )
        result = engine.can_flow_or_raise(
            source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
        )
        assert result.allowed


class TestFindRoutes:

    def test_find_routes(self, engine, principals):
        engine.create_channel(
            name="C1", source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id=principals.id,
        )
        engine.create_channel(
            name="C2", source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s3",
            owner_id=principals.id,
        )
        routes = engine.find_routes(
            source_type=FlowPointType.SCOPE, source_id="s1",
        )
        assert len(routes) == 2

    def test_find_routes_filtered_by_type(self, engine, principals):
        engine.create_channel(
            name="C1", source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s2",
            owner_id=principals.id,
            allowed_types=["Doc"],
        )
        engine.create_channel(
            name="C2", source_type=FlowPointType.SCOPE, source_id="s1",
            target_type=FlowPointType.SCOPE, target_id="s3",
            owner_id=principals.id,
            allowed_types=["Task"],
        )
        routes = engine.find_routes(
            source_type=FlowPointType.SCOPE, source_id="s1",
            object_type="Doc",
        )
        assert len(routes) == 1
        assert routes[0].name == "C1"

    def test_find_routes_empty(self, engine):
        routes = engine.find_routes(
            source_type=FlowPointType.SCOPE, source_id="nonexistent",
        )
        assert routes == []


class TestFlowResolution:

    def test_bool_true(self):
        r = FlowResolution(allowed=True)
        assert bool(r) is True

    def test_bool_false(self):
        r = FlowResolution(allowed=False, reason="blocked")
        assert bool(r) is False
