"""Tests for plugin lifecycle management."""

import pytest

from scoped.exceptions import PluginError
from scoped.identity.principal import PrincipalStore
from scoped.integrations.lifecycle import PluginLifecycleManager
from scoped.integrations.models import PluginState


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    return alice


@pytest.fixture
def plugins(sqlite_backend):
    return PluginLifecycleManager(sqlite_backend)


class TestInstallPlugin:

    def test_basic_install(self, plugins, principals):
        p = plugins.install_plugin(name="my-plugin", owner_id=principals.id)
        assert p.state == PluginState.INSTALLED
        assert p.name == "my-plugin"
        assert p.version == "0.1.0"

    def test_install_with_manifest(self, plugins, principals):
        manifest = {"permissions": [{"type": "scope_access", "target": "scope-1"}]}
        p = plugins.install_plugin(
            name="plugin-with-manifest", owner_id=principals.id,
            manifest=manifest,
        )
        assert p.manifest == manifest

    def test_install_with_version(self, plugins, principals):
        p = plugins.install_plugin(
            name="versioned", owner_id=principals.id, version="2.0.0",
        )
        assert p.version == "2.0.0"

    def test_install_with_scope(self, plugins, principals, sqlite_backend):
        from scoped.types import generate_id, now_utc
        sid = generate_id()
        sqlite_backend.execute(
            "INSERT INTO scopes (id, name, owner_id, created_at, lifecycle) VALUES (?, ?, ?, ?, ?)",
            (sid, "plugin-scope", principals.id, now_utc().isoformat(), "ACTIVE"),
        )
        p = plugins.install_plugin(
            name="scoped-plugin", owner_id=principals.id, scope_id=sid,
        )
        assert p.scope_id == sid

    def test_duplicate_name_fails(self, plugins, principals):
        plugins.install_plugin(name="unique", owner_id=principals.id)
        with pytest.raises(Exception):  # SQLite UNIQUE constraint
            plugins.install_plugin(name="unique", owner_id=principals.id)


class TestGetPlugin:

    def test_get_by_id(self, plugins, principals):
        created = plugins.install_plugin(name="test", owner_id=principals.id)
        fetched = plugins.get_plugin(created.id)
        assert fetched is not None
        assert fetched.id == created.id

    def test_get_by_name(self, plugins, principals):
        plugins.install_plugin(name="named-plugin", owner_id=principals.id)
        fetched = plugins.get_plugin_by_name("named-plugin")
        assert fetched is not None
        assert fetched.name == "named-plugin"

    def test_get_nonexistent(self, plugins):
        assert plugins.get_plugin("nope") is None

    def test_get_or_raise(self, plugins):
        with pytest.raises(PluginError, match="not found"):
            plugins.get_plugin_or_raise("nope")


class TestListPlugins:

    def test_list_all(self, plugins, principals):
        plugins.install_plugin(name="p1", owner_id=principals.id)
        plugins.install_plugin(name="p2", owner_id=principals.id)
        result = plugins.list_plugins()
        assert len(result) == 2

    def test_list_by_owner(self, plugins, principals):
        plugins.install_plugin(name="p1", owner_id=principals.id)
        result = plugins.list_plugins(owner_id=principals.id)
        assert len(result) == 1

    def test_list_by_state(self, plugins, principals):
        p = plugins.install_plugin(name="p1", owner_id=principals.id)
        plugins.install_plugin(name="p2", owner_id=principals.id)
        plugins.activate(p.id, actor_id=principals.id)
        result = plugins.list_plugins(state=PluginState.ACTIVE)
        assert len(result) == 1
        assert result[0].name == "p1"


class TestActivate:

    def test_activate_installed(self, plugins, principals):
        p = plugins.install_plugin(name="test", owner_id=principals.id)
        activated = plugins.activate(p.id, actor_id=principals.id)
        assert activated.state == PluginState.ACTIVE
        assert activated.activated_at is not None

    def test_activate_suspended(self, plugins, principals):
        p = plugins.install_plugin(name="test", owner_id=principals.id)
        plugins.activate(p.id, actor_id=principals.id)
        plugins.suspend(p.id, actor_id=principals.id)
        reactivated = plugins.activate(p.id, actor_id=principals.id)
        assert reactivated.state == PluginState.ACTIVE

    def test_cannot_activate_uninstalled(self, plugins, principals):
        p = plugins.install_plugin(name="test", owner_id=principals.id)
        plugins.activate(p.id, actor_id=principals.id)
        plugins.uninstall(p.id, actor_id=principals.id)
        with pytest.raises(PluginError, match="Cannot transition"):
            plugins.activate(p.id, actor_id=principals.id)

    def test_persists(self, plugins, principals):
        p = plugins.install_plugin(name="test", owner_id=principals.id)
        plugins.activate(p.id, actor_id=principals.id)
        fetched = plugins.get_plugin(p.id)
        assert fetched.state == PluginState.ACTIVE


class TestSuspend:

    def test_suspend_active(self, plugins, principals):
        p = plugins.install_plugin(name="test", owner_id=principals.id)
        plugins.activate(p.id, actor_id=principals.id)
        suspended = plugins.suspend(p.id, actor_id=principals.id)
        assert suspended.state == PluginState.SUSPENDED

    def test_cannot_suspend_installed(self, plugins, principals):
        p = plugins.install_plugin(name="test", owner_id=principals.id)
        with pytest.raises(PluginError, match="Cannot transition"):
            plugins.suspend(p.id, actor_id=principals.id)

    def test_suspend_deactivates_hooks(self, sqlite_backend, plugins, principals):
        p = plugins.install_plugin(name="test", owner_id=principals.id)
        plugins.activate(p.id, actor_id=principals.id)

        # Register a hook directly in DB
        from scoped.integrations.hooks import HookRegistry
        hooks = HookRegistry(sqlite_backend)
        hook = hooks.register_hook(
            plugin_id=p.id, hook_point="post_create",
            handler_ref="scoped:function:test:handler:1",
        )
        assert hook.is_active

        plugins.suspend(p.id, actor_id=principals.id)

        # Hook should be archived
        remaining = hooks.get_hooks_for_plugin(p.id, active_only=True)
        assert len(remaining) == 0


class TestUninstall:

    def test_uninstall_active(self, plugins, principals):
        p = plugins.install_plugin(name="test", owner_id=principals.id)
        plugins.activate(p.id, actor_id=principals.id)
        uninstalled = plugins.uninstall(p.id, actor_id=principals.id)
        assert uninstalled.state == PluginState.UNINSTALLED
        assert uninstalled.is_uninstalled

    def test_uninstall_suspended(self, plugins, principals):
        p = plugins.install_plugin(name="test", owner_id=principals.id)
        plugins.activate(p.id, actor_id=principals.id)
        plugins.suspend(p.id, actor_id=principals.id)
        uninstalled = plugins.uninstall(p.id, actor_id=principals.id)
        assert uninstalled.state == PluginState.UNINSTALLED

    def test_uninstall_revokes_permissions(self, plugins, principals):
        p = plugins.install_plugin(name="test", owner_id=principals.id)
        plugins.grant_permission(
            plugin_id=p.id, permission_type="scope_access",
            target_ref="scope-1", granted_by=principals.id,
        )
        plugins.activate(p.id, actor_id=principals.id)
        plugins.uninstall(p.id, actor_id=principals.id)

        perms = plugins.get_permissions(p.id, active_only=True)
        assert len(perms) == 0

    def test_uninstall_deactivates_hooks(self, sqlite_backend, plugins, principals):
        p = plugins.install_plugin(name="test", owner_id=principals.id)
        plugins.activate(p.id, actor_id=principals.id)

        from scoped.integrations.hooks import HookRegistry
        hooks = HookRegistry(sqlite_backend)
        hooks.register_hook(
            plugin_id=p.id, hook_point="post_create",
            handler_ref="scoped:function:test:handler:1",
        )

        plugins.uninstall(p.id, actor_id=principals.id)
        remaining = hooks.get_hooks_for_plugin(p.id, active_only=True)
        assert len(remaining) == 0

    def test_cannot_transition_after_uninstall(self, plugins, principals):
        p = plugins.install_plugin(name="test", owner_id=principals.id)
        plugins.activate(p.id, actor_id=principals.id)
        plugins.uninstall(p.id, actor_id=principals.id)
        with pytest.raises(PluginError, match="Cannot transition"):
            plugins.activate(p.id, actor_id=principals.id)


class TestPermissions:

    def test_grant_permission(self, plugins, principals):
        p = plugins.install_plugin(name="test", owner_id=principals.id)
        perm = plugins.grant_permission(
            plugin_id=p.id, permission_type="scope_access",
            target_ref="scope-1", granted_by=principals.id,
        )
        assert perm.permission_type == "scope_access"
        assert perm.target_ref == "scope-1"
        assert perm.is_active

    def test_revoke_permission(self, plugins, principals):
        p = plugins.install_plugin(name="test", owner_id=principals.id)
        perm = plugins.grant_permission(
            plugin_id=p.id, permission_type="scope_access",
            target_ref="scope-1", granted_by=principals.id,
        )
        plugins.revoke_permission(perm.id)
        perms = plugins.get_permissions(p.id, active_only=True)
        assert len(perms) == 0

    def test_get_permissions_includes_revoked(self, plugins, principals):
        p = plugins.install_plugin(name="test", owner_id=principals.id)
        perm = plugins.grant_permission(
            plugin_id=p.id, permission_type="scope_access",
            target_ref="scope-1", granted_by=principals.id,
        )
        plugins.revoke_permission(perm.id)
        perms = plugins.get_permissions(p.id, active_only=False)
        assert len(perms) == 1

    def test_has_permission(self, plugins, principals):
        p = plugins.install_plugin(name="test", owner_id=principals.id)
        assert not plugins.has_permission(p.id, "scope_access", "scope-1")
        plugins.grant_permission(
            plugin_id=p.id, permission_type="scope_access",
            target_ref="scope-1", granted_by=principals.id,
        )
        assert plugins.has_permission(p.id, "scope_access", "scope-1")

    def test_grant_multiple_permissions(self, plugins, principals):
        p = plugins.install_plugin(name="test", owner_id=principals.id)
        plugins.grant_permission(
            plugin_id=p.id, permission_type="scope_access",
            target_ref="scope-1", granted_by=principals.id,
        )
        plugins.grant_permission(
            plugin_id=p.id, permission_type="object_type",
            target_ref="Document", granted_by=principals.id,
        )
        perms = plugins.get_permissions(p.id)
        assert len(perms) == 2
