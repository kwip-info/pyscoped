"""Tests for registry introspection (unregistered construct detection)."""

import types

import pytest

from scoped.registry.base import Registry
from scoped.registry.decorators import register
from scoped.registry.introspection import introspect_module
from scoped.registry.kinds import RegistryKind


def _make_module(name: str, contents: dict) -> types.ModuleType:
    """Create a fake module with the given contents."""
    mod = types.ModuleType(name)
    for k, v in contents.items():
        # Set __module__ so introspection sees them as belonging to this module
        if hasattr(v, "__module__"):
            v.__module__ = name
        setattr(mod, k, v)
    return mod


class TestIntrospection:
    def test_detects_unregistered_class(self, registry: Registry):
        class Unregistered:
            pass

        Unregistered.__module__ = "fake_module"

        mod = _make_module("fake_module", {"Unregistered": Unregistered})
        result = introspect_module(mod, registry=registry)

        assert "Unregistered" in result.unregistered
        assert not result.compliant

    def test_detects_registered_class(self, registry: Registry):
        @register(RegistryKind.CLASS, namespace="fake_module", registry=registry)
        class Registered:
            pass

        Registered.__module__ = "fake_module"

        mod = _make_module("fake_module", {"Registered": Registered})
        result = introspect_module(mod, registry=registry)

        assert "Registered" in result.registered
        assert result.compliant

    def test_skips_private_names(self, registry: Registry):
        class _Private:
            pass

        _Private.__module__ = "fake_module"

        mod = _make_module("fake_module", {"_Private": _Private})
        result = introspect_module(mod, registry=registry)

        assert "_Private" not in result.unregistered
        assert "_Private" not in result.registered

    def test_skip_list(self, registry: Registry):
        class Skipped:
            pass

        Skipped.__module__ = "fake_module"

        mod = _make_module("fake_module", {"Skipped": Skipped})
        result = introspect_module(mod, registry=registry, skip={"Skipped"})

        assert "Skipped" in result.skipped
        assert "Skipped" not in result.unregistered

    def test_coverage_calculation(self, registry: Registry):
        @register(RegistryKind.CLASS, namespace="fake_module", registry=registry)
        class A:
            pass

        class B:
            pass

        A.__module__ = "fake_module"
        B.__module__ = "fake_module"

        mod = _make_module("fake_module", {"A": A, "B": B})
        result = introspect_module(mod, registry=registry)

        assert result.coverage == 0.5
