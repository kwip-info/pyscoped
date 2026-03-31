"""Tests for plugin sandbox — permission enforcement."""

import pytest

from scoped.exceptions import PluginPermissionError, PluginSandboxError
from scoped.identity.principal import PrincipalStore
from scoped.integrations.lifecycle import PluginLifecycleManager
from scoped.integrations.sandbox import PluginSandbox
from scoped.integrations.models import PluginState


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    return store.create_principal(kind="user", display_name="Alice", principal_id="alice")


@pytest.fixture
def plugins(sqlite_backend):
    return PluginLifecycleManager(sqlite_backend)


@pytest.fixture
def sandbox(sqlite_backend):
    return PluginSandbox(sqlite_backend)


@pytest.fixture
def active_plugin(plugins, principals):
    p = plugins.install_plugin(name="sandbox-test", owner_id=principals.id)
    plugins.activate(p.id, actor_id=principals.id)
    return plugins.get_plugin(p.id)


class TestCheckPermission:

    def test_no_permission(self, sandbox, active_plugin):
        assert not sandbox.check_permission(
            active_plugin.id, "scope_access", "scope-1",
        )

    def test_with_permission(self, sandbox, plugins, active_plugin, principals):
        plugins.grant_permission(
            plugin_id=active_plugin.id, permission_type="scope_access",
            target_ref="scope-1", granted_by=principals.id,
        )
        assert sandbox.check_permission(
            active_plugin.id, "scope_access", "scope-1",
        )

    def test_revoked_permission(self, sandbox, plugins, active_plugin, principals):
        perm = plugins.grant_permission(
            plugin_id=active_plugin.id, permission_type="scope_access",
            target_ref="scope-1", granted_by=principals.id,
        )
        plugins.revoke_permission(perm.id)
        assert not sandbox.check_permission(
            active_plugin.id, "scope_access", "scope-1",
        )


class TestRequirePermission:

    def test_raises_without_permission(self, sandbox, active_plugin):
        with pytest.raises(PluginPermissionError, match="lacks permission"):
            sandbox.require_permission(
                active_plugin.id, "scope_access", "scope-1",
            )

    def test_passes_with_permission(self, sandbox, plugins, active_plugin, principals):
        plugins.grant_permission(
            plugin_id=active_plugin.id, permission_type="scope_access",
            target_ref="scope-1", granted_by=principals.id,
        )
        # Should not raise
        sandbox.require_permission(
            active_plugin.id, "scope_access", "scope-1",
        )


class TestRequireActive:

    def test_active_plugin_passes(self, sandbox, active_plugin):
        sandbox.require_active(active_plugin.id)  # no error

    def test_installed_plugin_fails(self, sandbox, plugins, principals):
        p = plugins.install_plugin(name="inactive", owner_id=principals.id)
        with pytest.raises(PluginSandboxError, match="not active"):
            sandbox.require_active(p.id)

    def test_suspended_plugin_fails(self, sandbox, plugins, principals):
        p = plugins.install_plugin(name="will-suspend", owner_id=principals.id)
        plugins.activate(p.id, actor_id=principals.id)
        plugins.suspend(p.id, actor_id=principals.id)
        with pytest.raises(PluginSandboxError, match="not active"):
            sandbox.require_active(p.id)

    def test_nonexistent_plugin_fails(self, sandbox):
        with pytest.raises(PluginSandboxError, match="not found"):
            sandbox.require_active("nonexistent")


class TestScopeAccess:

    def test_check_scope_access(self, sandbox, plugins, active_plugin, principals):
        assert not sandbox.check_scope_access(active_plugin.id, "scope-1")
        plugins.grant_permission(
            plugin_id=active_plugin.id, permission_type="scope_access",
            target_ref="scope-1", granted_by=principals.id,
        )
        assert sandbox.check_scope_access(active_plugin.id, "scope-1")

    def test_require_scope_access(self, sandbox, active_plugin):
        with pytest.raises(PluginPermissionError):
            sandbox.require_scope_access(active_plugin.id, "scope-1")


class TestObjectTypeAccess:

    def test_check_object_type(self, sandbox, plugins, active_plugin, principals):
        assert not sandbox.check_object_type_access(active_plugin.id, "Document")
        plugins.grant_permission(
            plugin_id=active_plugin.id, permission_type="object_type",
            target_ref="Document", granted_by=principals.id,
        )
        assert sandbox.check_object_type_access(active_plugin.id, "Document")


class TestSecretAccess:

    def test_check_secret_access(self, sandbox, plugins, active_plugin, principals):
        assert not sandbox.check_secret_access(active_plugin.id, "ref-123")
        plugins.grant_permission(
            plugin_id=active_plugin.id, permission_type="secret_access",
            target_ref="ref-123", granted_by=principals.id,
        )
        assert sandbox.check_secret_access(active_plugin.id, "ref-123")

    def test_require_secret_access(self, sandbox, active_plugin):
        with pytest.raises(PluginPermissionError):
            sandbox.require_secret_access(active_plugin.id, "ref-123")


class TestHookAccess:

    def test_check_hook_access(self, sandbox, plugins, active_plugin, principals):
        assert not sandbox.check_hook_access(active_plugin.id, "post_create")
        plugins.grant_permission(
            plugin_id=active_plugin.id, permission_type="hook",
            target_ref="post_create", granted_by=principals.id,
        )
        assert sandbox.check_hook_access(active_plugin.id, "post_create")


class TestGetAllowed:

    def test_get_allowed_scopes(self, sandbox, plugins, active_plugin, principals):
        assert sandbox.get_allowed_scopes(active_plugin.id) == []
        plugins.grant_permission(
            plugin_id=active_plugin.id, permission_type="scope_access",
            target_ref="scope-1", granted_by=principals.id,
        )
        plugins.grant_permission(
            plugin_id=active_plugin.id, permission_type="scope_access",
            target_ref="scope-2", granted_by=principals.id,
        )
        scopes = sandbox.get_allowed_scopes(active_plugin.id)
        assert set(scopes) == {"scope-1", "scope-2"}

    def test_get_allowed_object_types(self, sandbox, plugins, active_plugin, principals):
        assert sandbox.get_allowed_object_types(active_plugin.id) == []
        plugins.grant_permission(
            plugin_id=active_plugin.id, permission_type="object_type",
            target_ref="Document", granted_by=principals.id,
        )
        types = sandbox.get_allowed_object_types(active_plugin.id)
        assert types == ["Document"]
