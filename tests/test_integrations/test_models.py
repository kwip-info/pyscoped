"""Tests for integration & plugin data models."""

from datetime import datetime, timezone

import pytest

from scoped.integrations.models import (
    Integration,
    Plugin,
    PluginHook,
    PluginPermission,
    PluginState,
    VALID_PLUGIN_TRANSITIONS,
    integration_from_row,
    hook_from_row,
    permission_from_row,
    plugin_from_row,
)
from scoped.types import Lifecycle


class TestIntegration:

    def test_defaults(self):
        ts = datetime.now(timezone.utc)
        i = Integration(
            id="i1", name="github", integration_type="github",
            owner_id="alice", created_at=ts,
        )
        assert i.is_active
        assert i.scope_id is None
        assert i.config == {}
        assert i.credentials_ref is None
        assert i.metadata == {}

    def test_snapshot(self):
        ts = datetime.now(timezone.utc)
        i = Integration(
            id="i1", name="slack", integration_type="slack",
            owner_id="alice", created_at=ts, config={"channel": "#eng"},
        )
        snap = i.snapshot()
        assert snap["name"] == "slack"
        assert snap["config"] == {"channel": "#eng"}

    def test_from_row(self):
        row = {
            "id": "i1", "name": "gh", "description": "GitHub",
            "integration_type": "github", "owner_id": "alice",
            "scope_id": None, "config_json": '{"org": "acme"}',
            "credentials_ref": None, "created_at": "2026-01-01T00:00:00+00:00",
            "lifecycle": "ACTIVE", "metadata_json": "{}",
        }
        i = integration_from_row(row)
        assert i.name == "gh"
        assert i.config == {"org": "acme"}


class TestPlugin:

    def test_defaults(self):
        ts = datetime.now(timezone.utc)
        p = Plugin(id="p1", name="test-plugin", owner_id="alice", installed_at=ts)
        assert p.state == PluginState.INSTALLED
        assert not p.is_active
        assert not p.is_uninstalled
        assert p.version == "0.1.0"

    def test_can_transition(self):
        ts = datetime.now(timezone.utc)
        p = Plugin(id="p1", name="test", owner_id="alice", installed_at=ts)
        assert p.can_transition_to(PluginState.ACTIVE)
        assert not p.can_transition_to(PluginState.SUSPENDED)

    def test_active_transitions(self):
        ts = datetime.now(timezone.utc)
        p = Plugin(
            id="p1", name="test", owner_id="alice",
            installed_at=ts, state=PluginState.ACTIVE,
        )
        assert p.is_active
        assert p.can_transition_to(PluginState.SUSPENDED)
        assert p.can_transition_to(PluginState.UNINSTALLED)
        assert not p.can_transition_to(PluginState.INSTALLED)

    def test_uninstalled_no_transitions(self):
        ts = datetime.now(timezone.utc)
        p = Plugin(
            id="p1", name="test", owner_id="alice",
            installed_at=ts, state=PluginState.UNINSTALLED,
        )
        assert p.is_uninstalled
        assert not p.can_transition_to(PluginState.ACTIVE)

    def test_snapshot(self):
        ts = datetime.now(timezone.utc)
        p = Plugin(
            id="p1", name="test", owner_id="alice",
            installed_at=ts, manifest={"hooks": ["post_create"]},
        )
        snap = p.snapshot()
        assert snap["state"] == "installed"
        assert snap["manifest"] == {"hooks": ["post_create"]}

    def test_from_row(self):
        row = {
            "id": "p1", "name": "test-plugin", "description": "",
            "version": "1.0.0", "owner_id": "alice", "scope_id": None,
            "manifest_json": '{"permissions": []}', "state": "active",
            "installed_at": "2026-01-01T00:00:00+00:00",
            "activated_at": "2026-01-01T01:00:00+00:00",
            "metadata_json": "{}",
        }
        p = plugin_from_row(row)
        assert p.name == "test-plugin"
        assert p.state == PluginState.ACTIVE
        assert p.activated_at is not None


class TestPluginHook:

    def test_defaults(self):
        h = PluginHook(
            id="h1", plugin_id="p1",
            hook_point="post_create", handler_ref="scoped:function:test:handler:1",
        )
        assert h.is_active
        assert h.priority == 0

    def test_from_row(self):
        row = {
            "id": "h1", "plugin_id": "p1",
            "hook_point": "post_create",
            "handler_ref": "scoped:function:test:handler:1",
            "priority": 10, "lifecycle": "ACTIVE",
        }
        h = hook_from_row(row)
        assert h.priority == 10
        assert h.hook_point == "post_create"


class TestPluginPermission:

    def test_defaults(self):
        ts = datetime.now(timezone.utc)
        p = PluginPermission(
            id="perm1", plugin_id="p1",
            permission_type="scope_access", target_ref="scope-1",
            granted_at=ts, granted_by="admin",
        )
        assert p.is_active

    def test_from_row(self):
        row = {
            "id": "perm1", "plugin_id": "p1",
            "permission_type": "object_type", "target_ref": "Document",
            "granted_at": "2026-01-01T00:00:00+00:00",
            "granted_by": "admin", "lifecycle": "ACTIVE",
        }
        p = permission_from_row(row)
        assert p.permission_type == "object_type"
        assert p.target_ref == "Document"


class TestValidTransitions:

    def test_installed_transitions(self):
        allowed = VALID_PLUGIN_TRANSITIONS[PluginState.INSTALLED]
        assert PluginState.ACTIVE in allowed
        assert PluginState.UNINSTALLED in allowed

    def test_suspended_can_reactivate(self):
        allowed = VALID_PLUGIN_TRANSITIONS[PluginState.SUSPENDED]
        assert PluginState.ACTIVE in allowed
        assert PluginState.UNINSTALLED in allowed

    def test_uninstalled_is_terminal(self):
        allowed = VALID_PLUGIN_TRANSITIONS[PluginState.UNINSTALLED]
        assert len(allowed) == 0
