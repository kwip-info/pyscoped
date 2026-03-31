"""Hook registry — register, dispatch, and execute plugin hooks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from scoped.exceptions import HookExecutionError, PluginError
from scoped.integrations.models import (
    PluginHook,
    PluginState,
    hook_from_row,
    plugin_from_row,
)
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc


@dataclass(frozen=True, slots=True)
class HookResult:
    """Result of a single hook execution."""

    hook_id: str
    plugin_id: str
    hook_point: str
    success: bool
    result: Any = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Aggregate result of dispatching a hook point to all registered plugins."""

    hook_point: str
    results: tuple[HookResult, ...] = ()
    all_succeeded: bool = True
    failed_count: int = 0


class HookRegistry:
    """Register and dispatch plugin hooks."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer
        # In-memory handler registry: handler_ref -> callable
        self._handlers: dict[str, Callable[..., Any]] = {}

    # -- Handler registration (in-memory) ----------------------------------

    def register_handler(self, handler_ref: str, handler: Callable[..., Any]) -> None:
        """Register a callable handler for a given handler_ref."""
        self._handlers[handler_ref] = handler

    def get_handler(self, handler_ref: str) -> Callable[..., Any] | None:
        return self._handlers.get(handler_ref)

    # -- Hook CRUD (persistent) --------------------------------------------

    def register_hook(
        self,
        *,
        plugin_id: str,
        hook_point: str,
        handler_ref: str,
        priority: int = 0,
    ) -> PluginHook:
        """Register a hook binding for a plugin."""
        # Verify plugin exists and is active
        row = self._backend.fetch_one(
            "SELECT * FROM plugins WHERE id = ?", (plugin_id,),
        )
        if row is None:
            raise PluginError(
                f"Plugin {plugin_id} not found",
                context={"plugin_id": plugin_id},
            )
        plugin = plugin_from_row(row)
        if plugin.state not in (PluginState.INSTALLED, PluginState.ACTIVE):
            raise PluginError(
                f"Cannot register hooks for plugin in state {plugin.state.value}",
                context={"plugin_id": plugin_id, "state": plugin.state.value},
            )

        hid = generate_id()
        hook = PluginHook(
            id=hid,
            plugin_id=plugin_id,
            hook_point=hook_point,
            handler_ref=handler_ref,
            priority=priority,
        )

        self._backend.execute(
            """INSERT INTO plugin_hooks
               (id, plugin_id, hook_point, handler_ref, priority, lifecycle)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (hid, plugin_id, hook_point, handler_ref, priority, "ACTIVE"),
        )

        return hook

    def deactivate_hook(self, hook_id: str) -> None:
        """Deactivate a specific hook."""
        self._backend.execute(
            "UPDATE plugin_hooks SET lifecycle = 'ARCHIVED' WHERE id = ?",
            (hook_id,),
        )

    def get_hooks_for_plugin(
        self,
        plugin_id: str,
        *,
        active_only: bool = True,
    ) -> list[PluginHook]:
        if active_only:
            rows = self._backend.fetch_all(
                "SELECT * FROM plugin_hooks WHERE plugin_id = ? AND lifecycle = 'ACTIVE'",
                (plugin_id,),
            )
        else:
            rows = self._backend.fetch_all(
                "SELECT * FROM plugin_hooks WHERE plugin_id = ?",
                (plugin_id,),
            )
        return [hook_from_row(r) for r in rows]

    def get_hooks_for_point(
        self,
        hook_point: str,
    ) -> list[PluginHook]:
        """Get all active hooks for a hook point, ordered by priority (highest first)."""
        rows = self._backend.fetch_all(
            """SELECT * FROM plugin_hooks
               WHERE hook_point = ? AND lifecycle = 'ACTIVE'
               ORDER BY priority DESC""",
            (hook_point,),
        )
        return [hook_from_row(r) for r in rows]

    # -- Dispatch ----------------------------------------------------------

    def dispatch(
        self,
        hook_point: str,
        *,
        context: dict[str, Any] | None = None,
        stop_on_failure: bool = False,
    ) -> DispatchResult:
        """Dispatch a hook point to all registered active plugins.

        Hooks are executed in priority order (highest first).
        Only active plugins have their hooks called.
        Failing hooks are caught and recorded — they don't crash the framework.
        """
        hooks = self.get_hooks_for_point(hook_point)
        ctx = context or {}
        results: list[HookResult] = []
        all_ok = True
        failed = 0

        for hook in hooks:
            # Verify the owning plugin is still active
            plugin_row = self._backend.fetch_one(
                "SELECT * FROM plugins WHERE id = ?", (hook.plugin_id,),
            )
            if plugin_row is None:
                continue
            plugin = plugin_from_row(plugin_row)
            if plugin.state != PluginState.ACTIVE:
                continue

            handler = self.get_handler(hook.handler_ref)
            if handler is None:
                hr = HookResult(
                    hook_id=hook.id,
                    plugin_id=hook.plugin_id,
                    hook_point=hook_point,
                    success=False,
                    error=f"Handler {hook.handler_ref} not found",
                )
                results.append(hr)
                all_ok = False
                failed += 1

                if self._audit is not None:
                    self._audit.record(
                        actor_id=hook.plugin_id,
                        action=ActionType.HOOK_EXECUTE,
                        target_type="hook",
                        target_id=hook.id,
                        after_state={"success": False, "error": hr.error},
                    )

                if stop_on_failure:
                    break
                continue

            # Execute the handler in a sandboxed try/except
            try:
                result_val = handler(ctx)
                hr = HookResult(
                    hook_id=hook.id,
                    plugin_id=hook.plugin_id,
                    hook_point=hook_point,
                    success=True,
                    result=result_val,
                )
                results.append(hr)

                if self._audit is not None:
                    self._audit.record(
                        actor_id=hook.plugin_id,
                        action=ActionType.HOOK_EXECUTE,
                        target_type="hook",
                        target_id=hook.id,
                        after_state={"success": True},
                    )

            except Exception as exc:
                hr = HookResult(
                    hook_id=hook.id,
                    plugin_id=hook.plugin_id,
                    hook_point=hook_point,
                    success=False,
                    error=str(exc),
                )
                results.append(hr)
                all_ok = False
                failed += 1

                if self._audit is not None:
                    self._audit.record(
                        actor_id=hook.plugin_id,
                        action=ActionType.HOOK_EXECUTE,
                        target_type="hook",
                        target_id=hook.id,
                        after_state={"success": False, "error": str(exc)},
                    )

                if stop_on_failure:
                    break

        return DispatchResult(
            hook_point=hook_point,
            results=tuple(results),
            all_succeeded=all_ok,
            failed_count=failed,
        )

    def dispatch_or_raise(
        self,
        hook_point: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> DispatchResult:
        """Dispatch and raise HookExecutionError if any hook fails."""
        result = self.dispatch(hook_point, context=context, stop_on_failure=True)
        if not result.all_succeeded:
            failed_hooks = [r for r in result.results if not r.success]
            errors = "; ".join(r.error or "unknown" for r in failed_hooks)
            raise HookExecutionError(
                f"Hook execution failed at {hook_point}: {errors}",
                context={
                    "hook_point": hook_point,
                    "failed_count": result.failed_count,
                },
            )
        return result
