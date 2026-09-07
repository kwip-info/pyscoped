"""Tests for the core Registry and RegistryEntry."""

import pytest

from scoped.exceptions import (
    AlreadyRegisteredError,
    NotRegisteredError,
    RegistryFrozenError,
)
from scoped.registry.base import Registry, RegistryEntry, get_registry
from scoped.registry.kinds import CustomKind, RegistryKind
from scoped.types import Lifecycle, URN


class TestURN:
    def test_create_and_str(self):
        urn = URN(kind="MODEL", namespace="myapp", name="User", version=1)
        assert str(urn) == "scoped:MODEL:myapp:User:1"

    def test_parse_valid(self):
        urn = URN.parse("scoped:FUNCTION:core:process:3")
        assert urn.kind == "FUNCTION"
        assert urn.namespace == "core"
        assert urn.name == "process"
        assert urn.version == 3

    def test_parse_invalid(self):
        with pytest.raises(ValueError, match="Invalid URN"):
            URN.parse("not:a:valid:urn")

    def test_frozen_and_hashable(self):
        urn = URN(kind="CLASS", namespace="app", name="Foo", version=1)
        assert hash(urn) is not None
        d = {urn: "test"}
        assert d[urn] == "test"


class TestRegistry:
    def test_register_and_get(self, registry: Registry):
        entry = registry.register(
            kind=RegistryKind.MODEL,
            namespace="myapp",
            name="User",
        )
        assert entry.id is not None
        assert entry.urn.kind == "MODEL"
        assert entry.urn.namespace == "myapp"
        assert entry.urn.name == "User"
        assert entry.lifecycle == Lifecycle.ACTIVE

        retrieved = registry.get(entry.id)
        assert retrieved is entry

    def test_register_duplicate_raises(self, registry: Registry):
        registry.register(kind=RegistryKind.MODEL, namespace="myapp", name="User")
        with pytest.raises(AlreadyRegisteredError):
            registry.register(kind=RegistryKind.MODEL, namespace="myapp", name="User")

    def test_get_nonexistent_raises(self, registry: Registry):
        with pytest.raises(NotRegisteredError):
            registry.get("nonexistent-id")

    def test_get_by_urn(self, registry: Registry):
        entry = registry.register(kind=RegistryKind.FUNCTION, namespace="core", name="process")
        urn = URN(kind="FUNCTION", namespace="core", name="process", version=1)
        assert registry.get_by_urn(urn) is entry
        assert registry.get_by_urn(str(urn)) is entry

    def test_get_by_target(self, registry: Registry):
        class MyClass:
            pass

        entry = registry.register(
            kind=RegistryKind.CLASS,
            namespace="app",
            name="MyClass",
            target=MyClass,
        )
        assert registry.get_by_target(MyClass) is entry

    def test_find_returns_none(self, registry: Registry):
        assert registry.find_by_urn("scoped:MODEL:x:y:1") is None
        assert registry.find_by_target(object()) is None

    def test_by_kind(self, registry: Registry):
        registry.register(kind=RegistryKind.MODEL, namespace="a", name="M1")
        registry.register(kind=RegistryKind.MODEL, namespace="a", name="M2")
        registry.register(kind=RegistryKind.FUNCTION, namespace="a", name="F1")

        models = registry.by_kind(RegistryKind.MODEL)
        assert len(models) == 2
        funcs = registry.by_kind(RegistryKind.FUNCTION)
        assert len(funcs) == 1

    def test_by_namespace(self, registry: Registry):
        registry.register(kind=RegistryKind.MODEL, namespace="app1", name="M1")
        registry.register(kind=RegistryKind.MODEL, namespace="app2", name="M2")

        assert len(registry.by_namespace("app1")) == 1
        assert len(registry.by_namespace("app2")) == 1
        assert len(registry.by_namespace("app3")) == 0

    def test_by_tag(self, registry: Registry):
        registry.register(
            kind=RegistryKind.MODEL,
            namespace="app",
            name="M1",
            tags={"important", "v2"},
        )
        registry.register(
            kind=RegistryKind.MODEL,
            namespace="app",
            name="M2",
            tags={"v2"},
        )

        assert len(registry.by_tag("important")) == 1
        assert len(registry.by_tag("v2")) == 2
        assert len(registry.by_tag("missing")) == 0

    def test_query_combined_filters(self, registry: Registry):
        registry.register(kind=RegistryKind.MODEL, namespace="app", name="M1", tags={"core"})
        registry.register(kind=RegistryKind.MODEL, namespace="app", name="M2")
        registry.register(kind=RegistryKind.FUNCTION, namespace="app", name="F1", tags={"core"})

        results = registry.query(kind=RegistryKind.MODEL, tag="core")
        assert len(results) == 1
        assert results[0].urn.name == "M1"

    def test_freeze_prevents_registration(self, registry: Registry):
        registry.freeze()
        with pytest.raises(RegistryFrozenError):
            registry.register(kind=RegistryKind.MODEL, namespace="app", name="M1")

    def test_unfreeze_allows_registration(self, registry: Registry):
        registry.freeze()
        registry.unfreeze()
        entry = registry.register(kind=RegistryKind.MODEL, namespace="app", name="M1")
        assert entry is not None

    def test_lifecycle_transition(self, registry: Registry):
        entry = registry.register(kind=RegistryKind.MODEL, namespace="app", name="M1")
        assert entry.lifecycle == Lifecycle.ACTIVE

        updated = registry.transition(entry.id, Lifecycle.DEPRECATED)
        assert updated.lifecycle == Lifecycle.DEPRECATED
        assert updated.entry_version == 2

    def test_archive(self, registry: Registry):
        entry = registry.register(kind=RegistryKind.MODEL, namespace="app", name="M1")
        archived = registry.archive(entry.id)
        assert archived.lifecycle == Lifecycle.ARCHIVED

    def test_contains_urn(self, registry: Registry):
        registry.register(kind=RegistryKind.MODEL, namespace="app", name="M1")
        assert registry.contains_urn("scoped:MODEL:app:M1:1")
        assert not registry.contains_urn("scoped:MODEL:app:M2:1")

    def test_count_and_all(self, registry: Registry):
        assert registry.count() == 0
        registry.register(kind=RegistryKind.MODEL, namespace="app", name="M1")
        registry.register(kind=RegistryKind.MODEL, namespace="app", name="M2")
        assert registry.count() == 2
        assert len(registry.all()) == 2

    def test_listener_called_on_register(self, registry: Registry):
        events = []
        registry.on_change(lambda event, entry: events.append((event, entry.urn.name)))

        registry.register(kind=RegistryKind.MODEL, namespace="app", name="M1")
        assert events == [("register", "M1")]

    def test_listener_called_on_lifecycle_change(self, registry: Registry):
        events = []
        registry.on_change(lambda event, entry: events.append((event, entry.lifecycle)))

        entry = registry.register(kind=RegistryKind.MODEL, namespace="app", name="M1")
        registry.transition(entry.id, Lifecycle.DEPRECATED)
        assert events[-1] == ("lifecycle_change", Lifecycle.DEPRECATED)

    def test_snapshot(self, registry: Registry):
        entry = registry.register(
            kind=RegistryKind.MODEL,
            namespace="app",
            name="M1",
            metadata={"key": "value"},
            tags={"tag1"},
        )
        snap = entry.snapshot()
        assert snap["urn"] == "scoped:MODEL:app:M1:1"
        assert snap["kind"] == "MODEL"
        assert snap["metadata"] == {"key": "value"}
        assert "tag1" in snap["tags"]


class TestCustomKind:
    def test_define_and_use(self, registry: Registry):
        webhook = CustomKind.define("WEBHOOK", "Webhook endpoint")
        entry = registry.register(kind=webhook, namespace="integrations", name="stripe_hook")
        assert entry.kind == webhook
        assert registry.by_kind(webhook) == [entry]

    def test_define_idempotent(self):
        k1 = CustomKind.define("TASK_QUEUE")
        k2 = CustomKind.define("TASK_QUEUE")
        assert k1 is k2

    def test_equality_and_hash(self):
        k1 = CustomKind.define("A")
        k2 = CustomKind("A")
        assert k1 == k2
        assert hash(k1) == hash(k2)


class TestGlobalRegistry:
    def test_get_registry_returns_singleton(self):
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2
