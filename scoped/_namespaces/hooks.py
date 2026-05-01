"""Hooks namespace — Layer 12 plugin hook registration and dispatch.

Usage::

    import scoped

    with scoped.as_principal(alice):
        scoped.hooks.register_handler("scoped:fn:on_create", handler)
        scoped.hooks.register(plugin, hook_point="post_object_create",
                              handler_ref="scoped:fn:on_create")

        scoped.hooks.dispatch("post_object_create", context={"object_id": "x"})
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from scoped._namespaces._base import _resolve_principal_id, _to_id

if TYPE_CHECKING:
    from scoped.integrations.hooks import DispatchResult
    from scoped.integrations.models import PluginHook


class HooksNamespace:
    """Simplified API for Layer 12 hook registry.

    Wraps ``HookRegistry`` with context-aware defaults.
    """

    def __init__(self, services: Any) -> None:
        self._svc = services

    def register_handler(
        self,
        handler_ref: str,
        handler: Callable[..., Any],
    ) -> None:
        """Register a callable in-memory under a handler_ref string.

        In-memory only — restart loses all handlers. Re-register handlers
        on every process boot.
        """
        self._svc.hooks.register_handler(handler_ref, handler)

    def register(
        self,
        plugin: str | Any,
        *,
        hook_point: str,
        handler_ref: str,
        actor_id: str | None = None,
        priority: int = 0,
    ) -> PluginHook:
        """Register a hook binding for a plugin (owner-only)."""
        return self._svc.hooks.register_hook(
            plugin_id=_to_id(plugin),
            hook_point=hook_point,
            handler_ref=handler_ref,
            actor_id=_resolve_principal_id(actor_id),
            priority=priority,
        )

    def deactivate(
        self,
        hook_id: str,
        *,
        actor_id: str | None = None,
    ) -> None:
        """Deactivate a specific hook (owner-only)."""
        self._svc.hooks.deactivate_hook(
            hook_id, actor_id=_resolve_principal_id(actor_id),
        )

    def for_plugin(
        self,
        plugin: str | Any,
        *,
        active_only: bool = True,
    ) -> list[PluginHook]:
        """List hooks registered for a plugin."""
        return self._svc.hooks.get_hooks_for_plugin(
            _to_id(plugin), active_only=active_only,
        )

    def for_point(self, hook_point: str) -> list[PluginHook]:
        """List active hooks bound to a hook point, ordered by priority."""
        return self._svc.hooks.get_hooks_for_point(hook_point)

    def dispatch(
        self,
        hook_point: str,
        *,
        context: dict[str, Any] | None = None,
        stop_on_failure: bool = False,
    ) -> DispatchResult:
        """Dispatch a hook point to all active plugin handlers."""
        return self._svc.hooks.dispatch(
            hook_point, context=context, stop_on_failure=stop_on_failure,
        )

    def dispatch_or_raise(
        self,
        hook_point: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> DispatchResult:
        """Dispatch and raise ``HookExecutionError`` if any hook fails."""
        return self._svc.hooks.dispatch_or_raise(hook_point, context=context)
