"""Tests for Layer 12 namespaces (plugins, integrations, hooks) and rule gating."""

import pytest

import scoped
from scoped.exceptions import AccessDeniedError
from scoped.integrations.models import PluginState
from scoped.rules.engine import RuleStore
from scoped.rules.models import BindingTargetType, RuleEffect, RuleType


@pytest.fixture
def client():
    return scoped.ScopedClient()


@pytest.fixture
def alice(client):
    return client.principals.create("Alice", principal_id="alice")


@pytest.fixture
def bob(client):
    return client.principals.create("Bob", principal_id="bob")


# ---------------------------------------------------------------------------
# Namespace + service wiring
# ---------------------------------------------------------------------------

class TestServicesWiring:

    def test_services_exposes_plugins(self, client):
        from scoped.integrations.lifecycle import PluginLifecycleManager
        assert isinstance(client.services.plugins, PluginLifecycleManager)

    def test_services_exposes_integrations(self, client):
        from scoped.integrations.connectors import IntegrationManager
        assert isinstance(client.services.integrations, IntegrationManager)

    def test_services_exposes_hooks(self, client):
        from scoped.integrations.hooks import HookRegistry
        assert isinstance(client.services.hooks, HookRegistry)

    def test_services_exposes_plugin_sandbox(self, client):
        from scoped.integrations.sandbox import PluginSandbox
        assert isinstance(client.services.plugin_sandbox, PluginSandbox)

    def test_hooks_share_sandbox_with_services(self, client):
        # Same sandbox instance — wiring should be one-and-the-same.
        assert client.services.hooks._sandbox is client.services.plugin_sandbox


class TestPluginsNamespace:

    def test_install_infers_owner_from_context(self, client, alice):
        with client.as_principal(alice):
            p = client.plugins.install("my-plugin")
        assert p.owner_id == alice.id
        assert p.state == PluginState.INSTALLED

    def test_activate_suspend_uninstall(self, client, alice):
        with client.as_principal(alice):
            p = client.plugins.install("p1")
            p = client.plugins.activate(p)
            assert p.state == PluginState.ACTIVE
            p = client.plugins.suspend(p)
            assert p.state == PluginState.SUSPENDED
            p = client.plugins.uninstall(p)
            assert p.state == PluginState.UNINSTALLED

    def test_grant_permission_inferred(self, client, alice):
        with client.as_principal(alice):
            p = client.plugins.install("p1")
            perm = client.plugins.grant_permission(
                p, permission_type="scope_access", target_ref="scope-1",
            )
        assert perm.granted_by == alice.id
        assert perm.is_active

    def test_get_by_name(self, client, alice):
        with client.as_principal(alice):
            client.plugins.install("findable")
        assert client.plugins.get_by_name("findable").name == "findable"


class TestIntegrationsNamespace:

    def test_create_infers_owner(self, client, alice):
        with client.as_principal(alice):
            i = client.integrations.create("gh", integration_type="github")
        assert i.owner_id == alice.id
        assert i.is_active

    def test_update_config_and_archive(self, client, alice):
        with client.as_principal(alice):
            i = client.integrations.create("gh", integration_type="github")
            i = client.integrations.update_config(i, {"org": "acme"})
            assert i.config["org"] == "acme"
            client.integrations.archive(i)
        fetched = client.integrations.get(i.id)
        assert not fetched.is_active

    def test_owner_enforced_through_namespace(self, client, alice, bob):
        with client.as_principal(alice):
            i = client.integrations.create("gh", integration_type="github")
        with client.as_principal(bob):
            with pytest.raises(AccessDeniedError, match="not the owner"):
                client.integrations.archive(i)


class TestHooksNamespace:

    def test_register_dispatch_via_namespace(self, client, alice):
        with client.as_principal(alice):
            p = client.plugins.install("hook-plugin")
            client.plugins.activate(p)

            client.hooks.register_handler(
                "scoped:fn:test:1", lambda ctx: ctx.get("v"),
            )
            client.hooks.register(
                p, hook_point="post_event",
                handler_ref="scoped:fn:test:1",
            )

            result = client.hooks.dispatch("post_event", context={"v": 42})
        assert result.all_succeeded
        assert result.results[0].result == 42

    def test_for_plugin_and_deactivate(self, client, alice):
        with client.as_principal(alice):
            p = client.plugins.install("hooks-life")
            client.plugins.activate(p)
            h = client.hooks.register(
                p, hook_point="x", handler_ref="scoped:fn:x:1",
            )
            assert len(client.hooks.for_plugin(p)) == 1
            client.hooks.deactivate(h.id)
            assert len(client.hooks.for_plugin(p)) == 0


# ---------------------------------------------------------------------------
# Rule engine integration
# ---------------------------------------------------------------------------

class TestRuleGating:

    def _bind_deny(self, client, *, action, target_type, target_id, name):
        """Helper: create a DENY rule on the given action and bind it."""
        store = RuleStore(client.services.backend)
        rule = store.create_rule(
            name=name,
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY,
            conditions={"action": action},
            priority=100,
            created_by="system",
        )
        store.bind_rule(
            rule.id,
            target_type=target_type,
            target_id=target_id,
            bound_by="system",
        )
        # Invalidate cache so subsequent evaluate() sees the new rule.
        if client.services.rule_engine._cache is not None:
            client.services.rule_engine._cache.clear()

    def test_deny_blocks_plugin_install(self, client, alice):
        self._bind_deny(
            client,
            action="plugin_install",
            target_type=BindingTargetType.OBJECT_TYPE,
            target_id="plugin",
            name="block-plugin-install",
        )
        with client.as_principal(alice):
            with pytest.raises(AccessDeniedError, match="plugin_install denied"):
                client.plugins.install("blocked")

    def test_deny_blocks_plugin_activate(self, client, alice):
        with client.as_principal(alice):
            p = client.plugins.install("p1")

        self._bind_deny(
            client,
            action="plugin_activate",
            target_type=BindingTargetType.OBJECT_TYPE,
            target_id="plugin",
            name="block-plugin-activate",
        )
        with client.as_principal(alice):
            with pytest.raises(AccessDeniedError, match="plugin_activate denied"):
                client.plugins.activate(p)

    def test_deny_blocks_integration_connect(self, client, alice):
        self._bind_deny(
            client,
            action="integration_connect",
            target_type=BindingTargetType.OBJECT_TYPE,
            target_id="integration",
            name="block-integration-connect",
        )
        with client.as_principal(alice):
            with pytest.raises(AccessDeniedError, match="integration_connect denied"):
                client.integrations.create("gh", integration_type="github")

    def test_deny_blocks_hook_dispatch(self, client, alice):
        with client.as_principal(alice):
            p = client.plugins.install("p1")
            client.plugins.activate(p)
            client.hooks.register_handler("scoped:fn:1", lambda ctx: "ok")
            client.hooks.register(
                p, hook_point="critical_action",
                handler_ref="scoped:fn:1",
            )

        # Bind a DENY rule on the specific hook point (object_id=hook_point)
        store = RuleStore(client.services.backend)
        rule = store.create_rule(
            name="block-hook",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY,
            conditions={"action": "hook_dispatch"},
            priority=100,
            created_by="system",
        )
        store.bind_rule(
            rule.id,
            target_type=BindingTargetType.OBJECT,
            target_id="critical_action",
            bound_by="system",
        )
        if client.services.rule_engine._cache is not None:
            client.services.rule_engine._cache.clear()

        with client.as_principal(alice):
            with pytest.raises(AccessDeniedError, match="hook_dispatch denied"):
                client.hooks.dispatch("critical_action")

    def test_no_rules_allows_install(self, client, alice):
        """No rules bound => no gating, default behavior preserved."""
        with client.as_principal(alice):
            p = client.plugins.install("ungated")
        assert p.name == "ungated"


# ---------------------------------------------------------------------------
# Module-level proxies
# ---------------------------------------------------------------------------

class TestModuleLevelProxies:

    def test_module_namespaces_resolve(self):
        # Reset the default client and re-init to a fresh in-memory DB,
        # then verify that the documented module-level forms work.
        client = scoped.init()
        try:
            alice = client.principals.create("ModAlice", principal_id="modalice")
            with scoped.as_principal(alice):
                p = scoped.plugins.install("mod-test")
                assert p.owner_id == alice.id

                i = scoped.integrations.create("ext", integration_type="generic")
                assert i.owner_id == alice.id

                client.plugins.activate(p)
                scoped.hooks.register_handler("scoped:fn:mod:1", lambda ctx: 1)
                scoped.hooks.register(
                    p, hook_point="m_point",
                    handler_ref="scoped:fn:mod:1",
                )
                result = scoped.hooks.dispatch("m_point")
                assert result.all_succeeded
        finally:
            import scoped.client as _client_module

            _client_module._default_client = None
