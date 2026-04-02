"""Tests for 2E: Typed config values across all 4 config surfaces."""

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError


# -- Webhook Config ----------------------------------------------------------

class TestWebhookConfig:
    def test_parse_webhook_config(self):
        from scoped.events.config_types import parse_webhook_config
        config = parse_webhook_config({
            "headers": {"Authorization": "Bearer tok"},
            "timeout": 30,
        })
        assert config.headers == {"Authorization": "Bearer tok"}
        assert config.timeout == 30
        assert config.retry_policy.max_retries == 3

    def test_webhook_config_to_dict_model(self):
        from scoped.events.config_types import WebhookConfig, webhook_config_to_dict
        config = WebhookConfig(headers={"X-Key": "val"}, timeout=5)
        raw = webhook_config_to_dict(config)
        assert isinstance(raw, dict)
        assert raw["headers"] == {"X-Key": "val"}
        assert raw["timeout"] == 5

    def test_webhook_config_to_dict_passthrough(self):
        from scoped.events.config_types import webhook_config_to_dict
        raw = {"headers": {}, "timeout": 10}
        assert webhook_config_to_dict(raw) is raw

    def test_webhook_config_defaults(self):
        from scoped.events.config_types import WebhookConfig
        config = WebhookConfig()
        assert config.headers == {}
        assert config.auth_token is None
        assert config.timeout == 10
        assert config.retry_policy.max_retries == 3
        assert config.retry_policy.backoff_base == 60

    def test_webhook_endpoint_typed_config(self):
        from scoped.events.models import WebhookEndpoint
        from scoped.events.config_types import WebhookConfig
        from scoped.types import now_utc
        ep = WebhookEndpoint(
            id="ep1", name="test", owner_id="alice", url="https://example.com",
            config={"headers": {"X": "Y"}, "timeout": 15},
            scope_id=None, created_at=now_utc(),
        )
        typed = ep.typed_config
        assert isinstance(typed, WebhookConfig)
        assert typed.timeout == 15

    def test_webhook_endpoint_typed_config_fallback(self):
        from scoped.events.models import WebhookEndpoint
        from scoped.types import now_utc
        ep = WebhookEndpoint(
            id="ep1", name="test", owner_id="alice", url="https://example.com",
            config={"unknown_field_only": True, "timeout": "not_an_int_but_coerced"},
            scope_id=None, created_at=now_utc(),
        )
        # Should not raise — falls back gracefully
        result = ep.typed_config
        assert result is not None


# -- Gate Details ------------------------------------------------------------

class TestGateDetails:
    def test_parse_stage_check(self):
        from scoped.deployments.gate_types import parse_gate_details, StageCheckDetails
        from scoped.deployments.models import GateType
        details = parse_gate_details(
            {"required_stage": "review", "current_stage": "draft"},
            GateType.STAGE_CHECK,
        )
        assert isinstance(details, StageCheckDetails)
        assert details.required_stage == "review"
        assert details.passed is False

    def test_parse_rule_check(self):
        from scoped.deployments.gate_types import parse_gate_details, RuleCheckDetails
        from scoped.deployments.models import GateType
        details = parse_gate_details(
            {"rule_ids": ["r1", "r2"], "evaluation_result": "allowed"},
            GateType.RULE_CHECK,
        )
        assert isinstance(details, RuleCheckDetails)
        assert details.rule_ids == ["r1", "r2"]

    def test_parse_approval(self):
        from scoped.deployments.gate_types import parse_gate_details, ApprovalDetails
        from scoped.deployments.models import GateType
        details = parse_gate_details(
            {"approver_id": "alice", "comment": "LGTM"},
            GateType.APPROVAL,
        )
        assert isinstance(details, ApprovalDetails)
        assert details.approver_id == "alice"

    def test_parse_custom_gate(self):
        from scoped.deployments.gate_types import parse_gate_details
        from scoped.deployments.models import GateType
        details = parse_gate_details(
            {"custom_key": "custom_value"},
            GateType.CUSTOM,
        )
        assert details is not None

    def test_gate_details_to_dict_model(self):
        from scoped.deployments.gate_types import StageCheckDetails, gate_details_to_dict
        details = StageCheckDetails(required_stage="prod", current_stage="staging", passed=True)
        raw = gate_details_to_dict(details)
        assert raw["required_stage"] == "prod"
        assert raw["passed"] is True

    def test_gate_details_to_dict_passthrough(self):
        from scoped.deployments.gate_types import gate_details_to_dict
        raw = {"key": "val"}
        assert gate_details_to_dict(raw) is raw

    def test_deployment_gate_typed_details(self):
        from scoped.deployments.models import DeploymentGate, GateType
        from scoped.deployments.gate_types import StageCheckDetails
        from scoped.types import now_utc
        gate = DeploymentGate(
            id="g1", deployment_id="d1", gate_type=GateType.STAGE_CHECK,
            passed=False, checked_at=now_utc(),
            details={"required_stage": "review", "current_stage": "draft"},
        )
        typed = gate.typed_details
        assert isinstance(typed, StageCheckDetails)
        assert typed.required_stage == "review"


# -- Plugin Types ------------------------------------------------------------

class TestPluginTypes:
    def test_parse_plugin_manifest(self):
        from scoped.integrations.plugin_types import parse_plugin_manifest, PluginManifest
        manifest = parse_plugin_manifest({
            "entry_point": "myapp.plugin:setup",
            "hook_points": ["on_create", "on_delete"],
            "tags": ["audit"],
        })
        assert isinstance(manifest, PluginManifest)
        assert manifest.entry_point == "myapp.plugin:setup"
        assert len(manifest.hook_points) == 2

    def test_parse_plugin_metadata(self):
        from scoped.integrations.plugin_types import parse_plugin_metadata, PluginMetadata
        meta = parse_plugin_metadata({
            "author": "Alice",
            "license": "MIT",
            "keywords": ["audit", "compliance"],
        })
        assert isinstance(meta, PluginMetadata)
        assert meta.author == "Alice"
        assert meta.keywords == ["audit", "compliance"]

    def test_manifest_to_dict_round_trip(self):
        from scoped.integrations.plugin_types import (
            PluginManifest, plugin_manifest_to_dict, parse_plugin_manifest,
        )
        original = PluginManifest(entry_point="x:y", tags=["t1"])
        raw = plugin_manifest_to_dict(original)
        parsed = parse_plugin_manifest(raw)
        assert parsed.entry_point == "x:y"
        assert parsed.tags == ["t1"]

    def test_manifest_to_dict_passthrough(self):
        from scoped.integrations.plugin_types import plugin_manifest_to_dict
        raw = {"entry_point": "x:y"}
        assert plugin_manifest_to_dict(raw) is raw

    def test_plugin_typed_manifest(self):
        from scoped.integrations.models import Plugin
        from scoped.integrations.plugin_types import PluginManifest
        from scoped.types import now_utc
        plugin = Plugin(
            id="p1", name="test-plugin", owner_id="alice",
            installed_at=now_utc(),
            manifest={"entry_point": "a:b", "hook_points": ["on_create"]},
            metadata={"author": "Alice"},
        )
        assert isinstance(plugin.typed_manifest, PluginManifest)
        assert plugin.typed_manifest.entry_point == "a:b"

    def test_plugin_typed_metadata(self):
        from scoped.integrations.models import Plugin
        from scoped.integrations.plugin_types import PluginMetadata
        from scoped.types import now_utc
        plugin = Plugin(
            id="p1", name="test-plugin", owner_id="alice",
            installed_at=now_utc(),
            manifest={}, metadata={"author": "Bob", "license": "MIT"},
        )
        assert isinstance(plugin.typed_metadata, PluginMetadata)
        assert plugin.typed_metadata.author == "Bob"

    def test_plugin_typed_manifest_fallback(self):
        from scoped.integrations.models import Plugin
        from scoped.types import now_utc
        plugin = Plugin(
            id="p1", name="test-plugin", owner_id="alice",
            installed_at=now_utc(),
            manifest={"completely_custom": True},
        )
        # Should not raise — extra="allow" on manifest model
        result = plugin.typed_manifest
        assert result is not None


# -- Scope Setting Types -----------------------------------------------------

class TestScopeSettingTypes:
    def test_register_and_parse(self):
        from scoped.tenancy.config_types import (
            register_setting_type, parse_setting_value, _SETTING_TYPES,
        )

        class LimitConfig(BaseModel):
            model_config = ConfigDict(frozen=True)
            max_items: int = 100

        register_setting_type("limit", LimitConfig)
        try:
            result = parse_setting_value("limit", {"max_items": 50})
            assert isinstance(result, LimitConfig)
            assert result.max_items == 50
        finally:
            _SETTING_TYPES.pop("limit", None)

    def test_parse_unregistered_key_passthrough(self):
        from scoped.tenancy.config_types import parse_setting_value
        result = parse_setting_value("unregistered_key", {"x": 1})
        assert result == {"x": 1}

    def test_parse_scalar_passthrough(self):
        from scoped.tenancy.config_types import parse_setting_value, register_setting_type, _SETTING_TYPES

        class Dummy(BaseModel):
            x: int = 1

        register_setting_type("dummy", Dummy)
        try:
            # Scalar value passes through even if type is registered
            result = parse_setting_value("dummy", 42)
            assert result == 42
        finally:
            _SETTING_TYPES.pop("dummy", None)

    def test_setting_value_to_dict_model(self):
        from scoped.tenancy.config_types import setting_value_to_dict

        class MyConfig(BaseModel):
            mode: str = "light"

        result = setting_value_to_dict(MyConfig(mode="dark"))
        assert isinstance(result, dict)
        assert result["mode"] == "dark"

    def test_setting_value_to_dict_passthrough(self):
        from scoped.tenancy.config_types import setting_value_to_dict
        assert setting_value_to_dict(42) == 42
        assert setting_value_to_dict("hello") == "hello"
        raw = {"key": "val"}
        assert setting_value_to_dict(raw) is raw

    def test_config_store_accepts_pydantic_model(self):
        """ConfigStore.set() should accept a Pydantic model as value."""
        from scoped.identity.principal import PrincipalStore
        from scoped.storage.sa_sqlite import SASQLiteBackend
        from scoped.tenancy.config import ConfigStore
        from scoped.tenancy.lifecycle import ScopeLifecycle

        backend = SASQLiteBackend(":memory:")
        backend.initialize()
        try:
            PrincipalStore(backend).create_principal(
                kind="user", display_name="Alice", principal_id="alice",
            )
            scope = ScopeLifecycle(backend).create_scope(name="test", owner_id="alice")

            class ThemeConfig(BaseModel):
                mode: str = "dark"

            config = ConfigStore(backend)
            setting = config.set(
                scope.id, key="theme", value=ThemeConfig(mode="dark"),
                principal_id="alice",
            )
            assert setting.value == {"mode": "dark"}
        finally:
            backend.close()
