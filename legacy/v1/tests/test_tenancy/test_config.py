"""Tests for configuration hierarchy — ConfigStore, ConfigResolver."""

import pytest

from scoped.exceptions import AccessDeniedError, ScopeFrozenError, ScopeNotFoundError
from scoped.identity.principal import PrincipalStore
from scoped.tenancy.config import (
    ConfigResolver,
    ConfigStore,
    ResolvedSetting,
    ScopedSetting,
    setting_from_row,
)
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.types import Lifecycle


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    bob = store.create_principal(kind="user", display_name="Bob", principal_id="bob")
    return alice, bob


@pytest.fixture
def lifecycle(sqlite_backend):
    return ScopeLifecycle(sqlite_backend)


@pytest.fixture
def config_store(sqlite_backend):
    return ConfigStore(sqlite_backend)


@pytest.fixture
def resolver(sqlite_backend):
    return ConfigResolver(sqlite_backend)


# -----------------------------------------------------------------------
# ScopedSetting model
# -----------------------------------------------------------------------

class TestScopedSettingModel:

    def test_snapshot(self):
        from scoped.types import now_utc

        s = ScopedSetting(
            id="s1", scope_id="sc1", key="theme",
            value="dark", created_at=now_utc(), created_by="alice",
            description="UI theme",
        )
        snap = s.snapshot()
        assert snap["key"] == "theme"
        assert snap["value"] == "dark"
        assert snap["lifecycle"] == "ACTIVE"

    def test_is_active(self):
        from scoped.types import now_utc

        s = ScopedSetting(
            id="s1", scope_id="sc1", key="k",
            value=1, created_at=now_utc(), created_by="a",
        )
        assert s.is_active
        # Frozen — can't set (frozen dataclass) but we can construct archived
        s2 = ScopedSetting(
            id="s2", scope_id="sc1", key="k",
            value=1, created_at=now_utc(), created_by="a",
            lifecycle=Lifecycle.ARCHIVED,
        )
        assert not s2.is_active

    def test_frozen(self):
        from scoped.types import now_utc

        s = ScopedSetting(
            id="s1", scope_id="sc1", key="k",
            value=1, created_at=now_utc(), created_by="a",
        )
        with pytest.raises(AttributeError):
            s.key = "other"


class TestSettingFromRow:

    def test_basic(self):
        row = {
            "id": "s1", "scope_id": "sc1", "key": "theme",
            "value_json": '"dark"', "description": "UI theme",
            "created_at": "2026-01-01T00:00:00+00:00",
            "created_by": "alice",
            "updated_at": None, "updated_by": None,
            "lifecycle": "ACTIVE",
        }
        s = setting_from_row(row)
        assert s.key == "theme"
        assert s.value == "dark"
        assert s.updated_at is None

    def test_with_update(self):
        row = {
            "id": "s1", "scope_id": "sc1", "key": "k",
            "value_json": "42",
            "description": "",
            "created_at": "2026-01-01T00:00:00+00:00",
            "created_by": "alice",
            "updated_at": "2026-01-02T00:00:00+00:00",
            "updated_by": "bob",
            "lifecycle": "ACTIVE",
        }
        s = setting_from_row(row)
        assert s.value == 42
        assert s.updated_by == "bob"


# -----------------------------------------------------------------------
# ConfigStore — set
# -----------------------------------------------------------------------

class TestConfigStoreSet:

    def test_set_creates_setting(self, config_store, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)

        setting = config_store.set(
            scope.id, key="theme", value="dark", principal_id=alice.id,
        )
        assert setting.key == "theme"
        assert setting.value == "dark"
        assert setting.scope_id == scope.id
        assert setting.created_by == alice.id

    def test_set_updates_existing(self, config_store, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)

        s1 = config_store.set(scope.id, key="k", value=1, principal_id=alice.id)
        s2 = config_store.set(scope.id, key="k", value=2, principal_id=alice.id)

        assert s2.id == s1.id  # same record updated
        assert s2.value == 2
        assert s2.updated_by == alice.id

    def test_set_json_types(self, config_store, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)

        # Test various JSON types
        config_store.set(scope.id, key="str_val", value="hello", principal_id=alice.id)
        config_store.set(scope.id, key="int_val", value=42, principal_id=alice.id)
        config_store.set(scope.id, key="bool_val", value=True, principal_id=alice.id)
        config_store.set(scope.id, key="list_val", value=[1, 2, 3], principal_id=alice.id)
        config_store.set(scope.id, key="dict_val", value={"a": 1}, principal_id=alice.id)
        config_store.set(scope.id, key="null_val", value=None, principal_id=alice.id)

        assert config_store.get(scope.id, "str_val").value == "hello"
        assert config_store.get(scope.id, "int_val").value == 42
        assert config_store.get(scope.id, "bool_val").value is True
        assert config_store.get(scope.id, "list_val").value == [1, 2, 3]
        assert config_store.get(scope.id, "dict_val").value == {"a": 1}
        assert config_store.get(scope.id, "null_val").value is None

    def test_set_with_description(self, config_store, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)

        setting = config_store.set(
            scope.id, key="k", value=1, principal_id=alice.id,
            description="Rate limit per minute",
        )
        assert setting.description == "Rate limit per minute"

    def test_set_nonexistent_scope_raises(self, config_store, principals):
        alice, _ = principals
        with pytest.raises(ScopeNotFoundError):
            config_store.set("no-scope", key="k", value=1, principal_id=alice.id)

    def test_set_non_owner_raises(self, config_store, lifecycle, principals):
        alice, bob = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)

        with pytest.raises(AccessDeniedError):
            config_store.set(scope.id, key="k", value=1, principal_id=bob.id)

    def test_set_frozen_scope_raises(self, config_store, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        lifecycle.freeze_scope(scope.id, frozen_by=alice.id)

        with pytest.raises(ScopeFrozenError):
            config_store.set(scope.id, key="k", value=1, principal_id=alice.id)

    def test_set_archived_scope_raises(self, config_store, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        lifecycle.archive_scope(scope.id, archived_by=alice.id)

        with pytest.raises(ScopeFrozenError):
            config_store.set(scope.id, key="k", value=1, principal_id=alice.id)


# -----------------------------------------------------------------------
# ConfigStore — get
# -----------------------------------------------------------------------

class TestConfigStoreGet:

    def test_get_existing(self, config_store, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        config_store.set(scope.id, key="k", value=42, principal_id=alice.id)

        s = config_store.get(scope.id, "k")
        assert s is not None
        assert s.value == 42

    def test_get_nonexistent_returns_none(self, config_store, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        assert config_store.get(scope.id, "nope") is None

    def test_get_archived_returns_none(self, config_store, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        config_store.set(scope.id, key="k", value=1, principal_id=alice.id)
        config_store.delete(scope.id, key="k", principal_id=alice.id)

        assert config_store.get(scope.id, "k") is None


# -----------------------------------------------------------------------
# ConfigStore — delete
# -----------------------------------------------------------------------

class TestConfigStoreDelete:

    def test_delete_existing(self, config_store, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        config_store.set(scope.id, key="k", value=1, principal_id=alice.id)

        assert config_store.delete(scope.id, key="k", principal_id=alice.id) is True
        assert config_store.get(scope.id, "k") is None

    def test_delete_nonexistent_returns_false(self, config_store, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        assert config_store.delete(scope.id, key="nope", principal_id=alice.id) is False

    def test_delete_non_owner_raises(self, config_store, lifecycle, principals):
        alice, bob = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        config_store.set(scope.id, key="k", value=1, principal_id=alice.id)

        with pytest.raises(AccessDeniedError):
            config_store.delete(scope.id, key="k", principal_id=bob.id)

    def test_delete_frozen_scope_raises(self, config_store, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        config_store.set(scope.id, key="k", value=1, principal_id=alice.id)
        lifecycle.freeze_scope(scope.id, frozen_by=alice.id)

        with pytest.raises(ScopeFrozenError):
            config_store.delete(scope.id, key="k", principal_id=alice.id)


# -----------------------------------------------------------------------
# ConfigStore — list_settings
# -----------------------------------------------------------------------

class TestConfigStoreList:

    def test_list_settings(self, config_store, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        config_store.set(scope.id, key="a", value=1, principal_id=alice.id)
        config_store.set(scope.id, key="b", value=2, principal_id=alice.id)
        config_store.set(scope.id, key="c", value=3, principal_id=alice.id)

        settings = config_store.list_settings(scope.id)
        assert len(settings) == 3
        assert [s.key for s in settings] == ["a", "b", "c"]  # sorted by key

    def test_list_excludes_archived(self, config_store, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        config_store.set(scope.id, key="a", value=1, principal_id=alice.id)
        config_store.set(scope.id, key="b", value=2, principal_id=alice.id)
        config_store.delete(scope.id, key="b", principal_id=alice.id)

        settings = config_store.list_settings(scope.id)
        assert len(settings) == 1

    def test_list_include_archived(self, config_store, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        config_store.set(scope.id, key="a", value=1, principal_id=alice.id)
        config_store.set(scope.id, key="b", value=2, principal_id=alice.id)
        config_store.delete(scope.id, key="b", principal_id=alice.id)

        settings = config_store.list_settings(scope.id, include_archived=True)
        assert len(settings) == 2

    def test_list_empty(self, config_store, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        assert config_store.list_settings(scope.id) == []


# -----------------------------------------------------------------------
# ConfigResolver — single key
# -----------------------------------------------------------------------

class TestConfigResolverResolve:

    def test_resolve_from_own_scope(self, config_store, resolver, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        config_store.set(scope.id, key="theme", value="dark", principal_id=alice.id)

        result = resolver.resolve(scope.id, "theme")
        assert result is not None
        assert result.value == "dark"
        assert result.source_scope_id == scope.id
        assert result.inherited is False

    def test_resolve_inherited_from_parent(self, config_store, resolver, lifecycle, principals):
        alice, _ = principals
        parent = lifecycle.create_scope(name="org", owner_id=alice.id)
        child = lifecycle.create_scope(name="team", owner_id=alice.id, parent_scope_id=parent.id)

        config_store.set(parent.id, key="theme", value="dark", principal_id=alice.id)

        result = resolver.resolve(child.id, "theme")
        assert result is not None
        assert result.value == "dark"
        assert result.source_scope_id == parent.id
        assert result.inherited is True

    def test_resolve_child_overrides_parent(self, config_store, resolver, lifecycle, principals):
        alice, _ = principals
        parent = lifecycle.create_scope(name="org", owner_id=alice.id)
        child = lifecycle.create_scope(name="team", owner_id=alice.id, parent_scope_id=parent.id)

        config_store.set(parent.id, key="theme", value="dark", principal_id=alice.id)
        config_store.set(child.id, key="theme", value="light", principal_id=alice.id)

        result = resolver.resolve(child.id, "theme")
        assert result.value == "light"
        assert result.inherited is False

    def test_resolve_grandparent(self, config_store, resolver, lifecycle, principals):
        alice, _ = principals
        gp = lifecycle.create_scope(name="enterprise", owner_id=alice.id)
        parent = lifecycle.create_scope(name="org", owner_id=alice.id, parent_scope_id=gp.id)
        child = lifecycle.create_scope(name="team", owner_id=alice.id, parent_scope_id=parent.id)

        config_store.set(gp.id, key="max_users", value=1000, principal_id=alice.id)

        result = resolver.resolve(child.id, "max_users")
        assert result.value == 1000
        assert result.source_scope_id == gp.id
        assert result.inherited is True

    def test_resolve_not_found(self, resolver, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        assert resolver.resolve(scope.id, "nope") is None

    def test_resolve_skips_middle_scope(self, config_store, resolver, lifecycle, principals):
        alice, _ = principals
        gp = lifecycle.create_scope(name="enterprise", owner_id=alice.id)
        parent = lifecycle.create_scope(name="org", owner_id=alice.id, parent_scope_id=gp.id)
        child = lifecycle.create_scope(name="team", owner_id=alice.id, parent_scope_id=parent.id)

        # Set at grandparent only
        config_store.set(gp.id, key="k", value="gp_val", principal_id=alice.id)

        # Resolve from child — should inherit through parent (which has nothing)
        result = resolver.resolve(child.id, "k")
        assert result.value == "gp_val"
        assert result.inherited is True


# -----------------------------------------------------------------------
# ConfigResolver — resolve_all
# -----------------------------------------------------------------------

class TestConfigResolverResolveAll:

    def test_resolve_all_own_settings(self, config_store, resolver, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        config_store.set(scope.id, key="a", value=1, principal_id=alice.id)
        config_store.set(scope.id, key="b", value=2, principal_id=alice.id)

        result = resolver.resolve_all(scope.id)
        assert len(result) == 2
        assert result["a"].value == 1
        assert result["a"].inherited is False
        assert result["b"].value == 2

    def test_resolve_all_merges_parent(self, config_store, resolver, lifecycle, principals):
        alice, _ = principals
        parent = lifecycle.create_scope(name="org", owner_id=alice.id)
        child = lifecycle.create_scope(name="team", owner_id=alice.id, parent_scope_id=parent.id)

        config_store.set(parent.id, key="a", value=1, principal_id=alice.id)
        config_store.set(parent.id, key="b", value=2, principal_id=alice.id)
        config_store.set(child.id, key="b", value=20, principal_id=alice.id)
        config_store.set(child.id, key="c", value=3, principal_id=alice.id)

        result = resolver.resolve_all(child.id)
        assert len(result) == 3
        assert result["a"].value == 1
        assert result["a"].inherited is True
        assert result["b"].value == 20
        assert result["b"].inherited is False  # overridden
        assert result["c"].value == 3
        assert result["c"].inherited is False

    def test_resolve_all_three_levels(self, config_store, resolver, lifecycle, principals):
        alice, _ = principals
        gp = lifecycle.create_scope(name="enterprise", owner_id=alice.id)
        parent = lifecycle.create_scope(name="org", owner_id=alice.id, parent_scope_id=gp.id)
        child = lifecycle.create_scope(name="team", owner_id=alice.id, parent_scope_id=parent.id)

        config_store.set(gp.id, key="global_config", value="gp", principal_id=alice.id)
        config_store.set(parent.id, key="org_config", value="parent", principal_id=alice.id)
        config_store.set(child.id, key="team_config", value="child", principal_id=alice.id)

        result = resolver.resolve_all(child.id)
        assert len(result) == 3
        assert result["global_config"].inherited is True
        assert result["org_config"].inherited is True
        assert result["team_config"].inherited is False

    def test_resolve_all_empty(self, resolver, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        assert resolver.resolve_all(scope.id) == {}


# -----------------------------------------------------------------------
# ConfigResolver — effective_value
# -----------------------------------------------------------------------

class TestConfigResolverEffectiveValue:

    def test_effective_value(self, config_store, resolver, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        config_store.set(scope.id, key="k", value=42, principal_id=alice.id)

        assert resolver.effective_value(scope.id, "k") == 42

    def test_effective_value_default(self, resolver, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        assert resolver.effective_value(scope.id, "nope", default="fallback") == "fallback"

    def test_effective_value_none_default(self, resolver, lifecycle, principals):
        alice, _ = principals
        scope = lifecycle.create_scope(name="team", owner_id=alice.id)
        assert resolver.effective_value(scope.id, "nope") is None

    def test_effective_value_inherited(self, config_store, resolver, lifecycle, principals):
        alice, _ = principals
        parent = lifecycle.create_scope(name="org", owner_id=alice.id)
        child = lifecycle.create_scope(name="team", owner_id=alice.id, parent_scope_id=parent.id)

        config_store.set(parent.id, key="k", value="from_parent", principal_id=alice.id)
        assert resolver.effective_value(child.id, "k") == "from_parent"
