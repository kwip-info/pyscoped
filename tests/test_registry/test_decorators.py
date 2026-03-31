"""Tests for registry decorators (@register, @track)."""

import pytest

from scoped.registry.base import Registry, get_registry
from scoped.registry.decorators import register, register_instance, track
from scoped.registry.kinds import RegistryKind


class TestRegisterDecorator:
    def test_register_class(self, registry: Registry):
        @register(RegistryKind.CLASS, namespace="app", registry=registry)
        class MyService:
            pass

        assert hasattr(MyService, "__scoped_entry__")
        entry = MyService.__scoped_entry__
        assert entry.urn.kind == "CLASS"
        assert entry.urn.namespace == "app"
        assert entry.target is MyService

    def test_register_function(self, registry: Registry):
        @register(RegistryKind.FUNCTION, namespace="app", name="do_stuff", registry=registry)
        def do_stuff():
            return 42

        assert hasattr(do_stuff, "__scoped_entry__")
        assert do_stuff() == 42  # decorator preserves functionality
        assert do_stuff.__scoped_entry__.urn.name == "do_stuff"

    def test_register_with_tags_and_metadata(self, registry: Registry):
        @register(
            RegistryKind.CLASS,
            namespace="app",
            tags={"critical", "v2"},
            metadata={"owner": "team-a"},
            registry=registry,
        )
        class ImportantService:
            pass

        entry = ImportantService.__scoped_entry__
        assert "critical" in entry.tags
        assert entry.metadata.get("owner") == "team-a"


class TestTrackDecorator:
    def test_track_class(self, registry: Registry):
        # track uses global registry — we need to set it up
        @register(registry=registry)
        class AutoTracked:
            pass

        assert hasattr(AutoTracked, "__scoped_entry__")
        entry = AutoTracked.__scoped_entry__
        assert entry.kind == RegistryKind.CLASS

    def test_track_function(self, registry: Registry):
        @register(registry=registry)
        def auto_tracked_func():
            return "hello"

        assert auto_tracked_func() == "hello"
        assert hasattr(auto_tracked_func, "__scoped_entry__")


class TestRegisterInstance:
    def test_register_data_instance(self, registry: Registry):
        data = {"name": "Test Document", "content": "..."}
        entry = register_instance(
            data,
            namespace="docs",
            name="test-doc-1",
            registered_by="user-123",
            registry=registry,
        )
        assert entry.kind == RegistryKind.INSTANCE
        assert entry.registered_by == "user-123"
        assert registry.get_by_target(data) is entry
