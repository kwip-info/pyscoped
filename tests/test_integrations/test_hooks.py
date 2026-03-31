"""Tests for hook registry and dispatch."""

import pytest

from scoped.exceptions import HookExecutionError, PluginError
from scoped.identity.principal import PrincipalStore
from scoped.integrations.hooks import HookRegistry
from scoped.integrations.lifecycle import PluginLifecycleManager
from scoped.integrations.models import PluginState


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    return store.create_principal(kind="user", display_name="Alice", principal_id="alice")


@pytest.fixture
def plugins(sqlite_backend):
    return PluginLifecycleManager(sqlite_backend)


@pytest.fixture
def hooks(sqlite_backend):
    return HookRegistry(sqlite_backend)


@pytest.fixture
def active_plugin(plugins, principals):
    """An active plugin ready for hook registration."""
    p = plugins.install_plugin(name="hook-plugin", owner_id=principals.id)
    plugins.activate(p.id, actor_id=principals.id)
    return plugins.get_plugin(p.id)


class TestRegisterHook:

    def test_basic_register(self, hooks, active_plugin):
        h = hooks.register_hook(
            plugin_id=active_plugin.id,
            hook_point="post_object_create",
            handler_ref="scoped:function:test:on_create:1",
        )
        assert h.hook_point == "post_object_create"
        assert h.plugin_id == active_plugin.id
        assert h.is_active

    def test_register_with_priority(self, hooks, active_plugin):
        h = hooks.register_hook(
            plugin_id=active_plugin.id,
            hook_point="post_create",
            handler_ref="scoped:function:test:handler:1",
            priority=100,
        )
        assert h.priority == 100

    def test_register_for_installed_plugin(self, hooks, plugins, principals):
        """Can register hooks for installed (not yet active) plugins too."""
        p = plugins.install_plugin(name="installed-only", owner_id=principals.id)
        h = hooks.register_hook(
            plugin_id=p.id,
            hook_point="post_create",
            handler_ref="scoped:function:test:handler:1",
        )
        assert h.is_active

    def test_register_for_nonexistent_plugin(self, hooks):
        with pytest.raises(PluginError, match="not found"):
            hooks.register_hook(
                plugin_id="nonexistent",
                hook_point="post_create",
                handler_ref="scoped:function:test:handler:1",
            )

    def test_register_for_uninstalled_plugin(self, hooks, plugins, principals):
        p = plugins.install_plugin(name="to-uninstall", owner_id=principals.id)
        plugins.activate(p.id, actor_id=principals.id)
        plugins.uninstall(p.id, actor_id=principals.id)
        with pytest.raises(PluginError, match="Cannot register hooks"):
            hooks.register_hook(
                plugin_id=p.id,
                hook_point="post_create",
                handler_ref="scoped:function:test:handler:1",
            )


class TestGetHooks:

    def test_get_for_plugin(self, hooks, active_plugin):
        hooks.register_hook(
            plugin_id=active_plugin.id,
            hook_point="post_create",
            handler_ref="scoped:function:test:h1:1",
        )
        hooks.register_hook(
            plugin_id=active_plugin.id,
            hook_point="pre_delete",
            handler_ref="scoped:function:test:h2:1",
        )
        result = hooks.get_hooks_for_plugin(active_plugin.id)
        assert len(result) == 2

    def test_get_for_point(self, hooks, active_plugin):
        hooks.register_hook(
            plugin_id=active_plugin.id,
            hook_point="post_create",
            handler_ref="scoped:function:test:h1:1",
        )
        result = hooks.get_hooks_for_point("post_create")
        assert len(result) == 1
        assert result[0].hook_point == "post_create"

    def test_get_for_point_ordered_by_priority(self, hooks, plugins, principals, active_plugin):
        # Create a second active plugin
        p2 = plugins.install_plugin(name="plugin-2", owner_id=principals.id)
        plugins.activate(p2.id, actor_id=principals.id)

        hooks.register_hook(
            plugin_id=active_plugin.id,
            hook_point="post_create",
            handler_ref="scoped:function:test:low:1",
            priority=10,
        )
        hooks.register_hook(
            plugin_id=p2.id,
            hook_point="post_create",
            handler_ref="scoped:function:test:high:1",
            priority=100,
        )

        result = hooks.get_hooks_for_point("post_create")
        assert len(result) == 2
        assert result[0].priority == 100  # highest first
        assert result[1].priority == 10

    def test_deactivate_hook(self, hooks, active_plugin):
        h = hooks.register_hook(
            plugin_id=active_plugin.id,
            hook_point="post_create",
            handler_ref="scoped:function:test:h1:1",
        )
        hooks.deactivate_hook(h.id)
        result = hooks.get_hooks_for_plugin(active_plugin.id, active_only=True)
        assert len(result) == 0

    def test_deactivated_not_in_point_query(self, hooks, active_plugin):
        h = hooks.register_hook(
            plugin_id=active_plugin.id,
            hook_point="post_create",
            handler_ref="scoped:function:test:h1:1",
        )
        hooks.deactivate_hook(h.id)
        result = hooks.get_hooks_for_point("post_create")
        assert len(result) == 0


class TestDispatch:

    def test_dispatch_no_hooks(self, hooks):
        result = hooks.dispatch("post_create")
        assert result.all_succeeded
        assert len(result.results) == 0

    def test_dispatch_successful(self, hooks, active_plugin):
        hooks.register_hook(
            plugin_id=active_plugin.id,
            hook_point="post_create",
            handler_ref="scoped:function:test:success:1",
        )
        hooks.register_handler(
            "scoped:function:test:success:1",
            lambda ctx: "ok",
        )

        result = hooks.dispatch("post_create", context={"object_id": "obj1"})
        assert result.all_succeeded
        assert len(result.results) == 1
        assert result.results[0].success
        assert result.results[0].result == "ok"

    def test_dispatch_handler_receives_context(self, hooks, active_plugin):
        received = {}
        def handler(ctx):
            received.update(ctx)
            return "done"

        hooks.register_hook(
            plugin_id=active_plugin.id,
            hook_point="post_create",
            handler_ref="scoped:function:test:ctx:1",
        )
        hooks.register_handler("scoped:function:test:ctx:1", handler)

        hooks.dispatch("post_create", context={"key": "value"})
        assert received == {"key": "value"}

    def test_dispatch_failing_hook(self, hooks, active_plugin):
        hooks.register_hook(
            plugin_id=active_plugin.id,
            hook_point="post_create",
            handler_ref="scoped:function:test:fail:1",
        )
        hooks.register_handler(
            "scoped:function:test:fail:1",
            lambda ctx: (_ for _ in ()).throw(ValueError("boom")),
        )

        result = hooks.dispatch("post_create")
        assert not result.all_succeeded
        assert result.failed_count == 1
        assert "boom" in result.results[0].error

    def test_dispatch_missing_handler(self, hooks, active_plugin):
        hooks.register_hook(
            plugin_id=active_plugin.id,
            hook_point="post_create",
            handler_ref="scoped:function:test:missing:1",
        )
        # Don't register the handler

        result = hooks.dispatch("post_create")
        assert not result.all_succeeded
        assert "not found" in result.results[0].error

    def test_dispatch_skips_inactive_plugin(self, hooks, plugins, principals, active_plugin):
        hooks.register_hook(
            plugin_id=active_plugin.id,
            hook_point="post_create",
            handler_ref="scoped:function:test:h:1",
        )
        hooks.register_handler("scoped:function:test:h:1", lambda ctx: "ok")

        # Suspend the plugin
        plugins.suspend(active_plugin.id, actor_id=principals.id)

        result = hooks.dispatch("post_create")
        # Plugin suspended → hook not called
        assert len(result.results) == 0

    def test_dispatch_multiple_hooks_priority_order(self, hooks, plugins, principals, active_plugin):
        p2 = plugins.install_plugin(name="plugin-2", owner_id=principals.id)
        plugins.activate(p2.id, actor_id=principals.id)

        order = []

        hooks.register_hook(
            plugin_id=active_plugin.id,
            hook_point="post_create",
            handler_ref="scoped:function:test:low:1",
            priority=10,
        )
        hooks.register_handler("scoped:function:test:low:1", lambda ctx: order.append("low"))

        hooks.register_hook(
            plugin_id=p2.id,
            hook_point="post_create",
            handler_ref="scoped:function:test:high:1",
            priority=100,
        )
        hooks.register_handler("scoped:function:test:high:1", lambda ctx: order.append("high"))

        result = hooks.dispatch("post_create")
        assert result.all_succeeded
        assert order == ["high", "low"]

    def test_dispatch_stop_on_failure(self, hooks, plugins, principals, active_plugin):
        p2 = plugins.install_plugin(name="plugin-2", owner_id=principals.id)
        plugins.activate(p2.id, actor_id=principals.id)

        hooks.register_hook(
            plugin_id=active_plugin.id,
            hook_point="post_create",
            handler_ref="scoped:function:test:fail:1",
            priority=100,  # runs first
        )
        hooks.register_handler(
            "scoped:function:test:fail:1",
            lambda ctx: (_ for _ in ()).throw(RuntimeError("fail")),
        )

        hooks.register_hook(
            plugin_id=p2.id,
            hook_point="post_create",
            handler_ref="scoped:function:test:ok:1",
            priority=10,  # would run second
        )
        hooks.register_handler("scoped:function:test:ok:1", lambda ctx: "ok")

        result = hooks.dispatch("post_create", stop_on_failure=True)
        assert not result.all_succeeded
        assert len(result.results) == 1  # stopped after first failure


class TestDispatchOrRaise:

    def test_success(self, hooks, active_plugin):
        hooks.register_hook(
            plugin_id=active_plugin.id,
            hook_point="pre_deploy",
            handler_ref="scoped:function:test:ok:1",
        )
        hooks.register_handler("scoped:function:test:ok:1", lambda ctx: "ok")

        result = hooks.dispatch_or_raise("pre_deploy")
        assert result.all_succeeded

    def test_raises_on_failure(self, hooks, active_plugin):
        hooks.register_hook(
            plugin_id=active_plugin.id,
            hook_point="pre_deploy",
            handler_ref="scoped:function:test:fail:1",
        )
        hooks.register_handler(
            "scoped:function:test:fail:1",
            lambda ctx: (_ for _ in ()).throw(ValueError("nope")),
        )

        with pytest.raises(HookExecutionError, match="pre_deploy"):
            hooks.dispatch_or_raise("pre_deploy")

    def test_no_hooks_succeeds(self, hooks):
        result = hooks.dispatch_or_raise("nonexistent_point")
        assert result.all_succeeded
