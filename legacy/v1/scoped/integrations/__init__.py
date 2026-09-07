"""Layer 12: Integrations & Plugins.

Integrations connect the Scoped world to external systems.
Plugins extend the framework with new behavior.
Both are first-class scoped citizens — registered, sandboxed,
permission-gated, and audited.
"""

from scoped.integrations.connectors import IntegrationManager
from scoped.integrations.hooks import DispatchResult, HookRegistry, HookResult
from scoped.integrations.lifecycle import PluginLifecycleManager
from scoped.integrations.models import (
    Integration,
    Plugin,
    PluginHook,
    PluginPermission,
    PluginState,
    VALID_PLUGIN_TRANSITIONS,
)
from scoped.integrations.sandbox import PluginSandbox

__all__ = [
    "DispatchResult",
    "HookRegistry",
    "HookResult",
    "Integration",
    "IntegrationManager",
    "Plugin",
    "PluginHook",
    "PluginLifecycleManager",
    "PluginPermission",
    "PluginSandbox",
    "PluginState",
    "VALID_PLUGIN_TRANSITIONS",
]
