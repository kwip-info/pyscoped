"""Tests for 4D: Config inheritance transparency — resolution_chain."""

import pytest

from scoped.identity.principal import PrincipalStore
from scoped.storage.sa_sqlite import SASQLiteBackend
from scoped.tenancy.config import ConfigResolver, ConfigStore
from scoped.tenancy.lifecycle import ScopeLifecycle


@pytest.fixture
def backend():
    b = SASQLiteBackend(":memory:")
    b.initialize()
    yield b
    b.close()


@pytest.fixture
def scopes(backend):
    # Create the "alice" principal so FK constraints pass
    ps = PrincipalStore(backend)
    ps.create_principal(kind="user", display_name="Alice", principal_id="alice")
    return ScopeLifecycle(backend)


@pytest.fixture
def config(backend):
    return ConfigStore(backend)


@pytest.fixture
def resolver(backend):
    return ConfigResolver(backend)


class TestResolutionChain:
    """Verify resolution_chain shows all ancestor values."""

    def test_single_scope_chain(self, scopes, config, resolver):
        root = scopes.create_scope(name="root", owner_id="alice")
        config.set(root.id, key="theme", value="dark", principal_id="alice")

        result = resolver.resolve(root.id, "theme")
        assert result is not None
        assert result.value == "dark"
        assert result.inherited is False
        assert result.resolution_chain == [(root.id, "dark")]

    def test_inherited_chain(self, scopes, config, resolver):
        root = scopes.create_scope(name="root", owner_id="alice")
        child = scopes.create_scope(
            name="child", owner_id="alice", parent_scope_id=root.id,
        )
        config.set(root.id, key="theme", value="dark", principal_id="alice")

        result = resolver.resolve(child.id, "theme")
        assert result is not None
        assert result.value == "dark"
        assert result.inherited is True
        assert result.resolution_chain == [(root.id, "dark")]

    def test_override_shows_both_values(self, scopes, config, resolver):
        root = scopes.create_scope(name="root", owner_id="alice")
        child = scopes.create_scope(
            name="child", owner_id="alice", parent_scope_id=root.id,
        )
        config.set(root.id, key="theme", value="dark", principal_id="alice")
        config.set(child.id, key="theme", value="light", principal_id="alice")

        result = resolver.resolve(child.id, "theme")
        assert result is not None
        assert result.value == "light"
        assert result.inherited is False
        # Chain shows root value first, then child override
        assert result.resolution_chain == [
            (root.id, "dark"),
            (child.id, "light"),
        ]

    def test_three_level_chain(self, scopes, config, resolver):
        root = scopes.create_scope(name="root", owner_id="alice")
        mid = scopes.create_scope(
            name="mid", owner_id="alice", parent_scope_id=root.id,
        )
        leaf = scopes.create_scope(
            name="leaf", owner_id="alice", parent_scope_id=mid.id,
        )

        config.set(root.id, key="limit", value=100, principal_id="alice")
        config.set(mid.id, key="limit", value=50, principal_id="alice")
        config.set(leaf.id, key="limit", value=25, principal_id="alice")

        result = resolver.resolve(leaf.id, "limit")
        assert result is not None
        assert result.value == 25
        assert result.inherited is False
        assert result.resolution_chain == [
            (root.id, 100),
            (mid.id, 50),
            (leaf.id, 25),
        ]

    def test_chain_skips_scopes_without_value(self, scopes, config, resolver):
        root = scopes.create_scope(name="root", owner_id="alice")
        mid = scopes.create_scope(
            name="mid", owner_id="alice", parent_scope_id=root.id,
        )
        leaf = scopes.create_scope(
            name="leaf", owner_id="alice", parent_scope_id=mid.id,
        )

        config.set(root.id, key="color", value="blue", principal_id="alice")
        # mid has no setting for "color"
        config.set(leaf.id, key="color", value="red", principal_id="alice")

        result = resolver.resolve(leaf.id, "color")
        assert result is not None
        assert result.value == "red"
        # Chain only includes scopes that have the key
        assert result.resolution_chain == [
            (root.id, "blue"),
            (leaf.id, "red"),
        ]

    def test_missing_key_returns_none(self, scopes, config, resolver):
        root = scopes.create_scope(name="root", owner_id="alice")
        result = resolver.resolve(root.id, "nonexistent")
        assert result is None


class TestResolveAllChain:
    """Verify resolve_all populates resolution_chain for each key."""

    def test_resolve_all_with_chain(self, scopes, config, resolver):
        root = scopes.create_scope(name="root", owner_id="alice")
        child = scopes.create_scope(
            name="child", owner_id="alice", parent_scope_id=root.id,
        )

        config.set(root.id, key="theme", value="dark", principal_id="alice")
        config.set(root.id, key="lang", value="en", principal_id="alice")
        config.set(child.id, key="theme", value="light", principal_id="alice")

        result = resolver.resolve_all(child.id)

        # "theme" overridden: chain has both
        assert result["theme"].value == "light"
        assert result["theme"].resolution_chain == [
            (root.id, "dark"),
            (child.id, "light"),
        ]

        # "lang" inherited: chain has only root
        assert result["lang"].value == "en"
        assert result["lang"].inherited is True
        assert result["lang"].resolution_chain == [(root.id, "en")]

    def test_resolve_all_empty(self, scopes, config, resolver):
        root = scopes.create_scope(name="root", owner_id="alice")
        result = resolver.resolve_all(root.id)
        assert result == {}
