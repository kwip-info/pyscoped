"""Decorators for auto-registering constructs.

Usage:
    from scoped.registry import register, track

    @register(kind=RegistryKind.CLASS, namespace="myapp")
    class MyService:
        ...

    @register(kind=RegistryKind.FUNCTION, namespace="myapp")
    def process_payment(order):
        ...

    @track  # shorthand — infers kind from the target, uses module as namespace
    class AnotherService:
        ...
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, TypeVar, overload

from scoped.registry.base import Registry, RegistryEntry, get_registry
from scoped.registry.kinds import CustomKind, RegistryKind

T = TypeVar("T")


def _infer_kind(target: Any) -> RegistryKind:
    """Infer the registry kind from a target object."""
    if inspect.isclass(target):
        return RegistryKind.CLASS
    elif inspect.isfunction(target) or inspect.ismethod(target):
        return RegistryKind.FUNCTION
    elif inspect.iscoroutinefunction(target):
        return RegistryKind.FUNCTION
    else:
        return RegistryKind.CLASS  # default fallback


def _infer_namespace(target: Any) -> str:
    """Infer namespace from the target's module."""
    module = getattr(target, "__module__", None)
    if module:
        parts = module.split(".")
        # Use the top-level package, or full module if flat
        return parts[0] if len(parts) > 1 else module
    return "unknown"


def _infer_name(target: Any) -> str:
    """Infer name from the target."""
    return getattr(target, "__qualname__", None) or getattr(target, "__name__", repr(target))


def register(
    kind: RegistryKind | CustomKind | None = None,
    *,
    namespace: str | None = None,
    name: str | None = None,
    tags: set[str] | None = None,
    metadata: dict[str, Any] | None = None,
    registry: Registry | None = None,
) -> Callable[[T], T]:
    """
    Decorator that registers a class or function in the registry.

    Can be used with explicit parameters or bare:
        @register(kind=RegistryKind.CLASS, namespace="myapp")
        class Foo: ...

        @register  # infers everything
        def bar(): ...
    """
    def decorator(target: T) -> T:
        reg = registry or get_registry()
        resolved_kind = kind or _infer_kind(target)
        resolved_namespace = namespace or _infer_namespace(target)
        resolved_name = name or _infer_name(target)

        entry = reg.register(
            kind=resolved_kind,
            namespace=resolved_namespace,
            name=resolved_name,
            target=target,
            tags=tags or set(),
            metadata=metadata or {},
        )

        # Attach the registry entry to the target for introspection
        target.__scoped_entry__ = entry  # type: ignore[attr-defined]
        return target

    return decorator


def track(target: T) -> T:
    """
    Bare decorator shorthand — registers with all inferred values.

    @track
    class MyClass: ...

    @track
    def my_function(): ...
    """
    return register()(target)


def register_instance(
    instance: Any,
    *,
    kind: RegistryKind = RegistryKind.INSTANCE,
    namespace: str,
    name: str,
    registered_by: str = "system",
    tags: set[str] | None = None,
    metadata: dict[str, Any] | None = None,
    registry: Registry | None = None,
) -> RegistryEntry:
    """
    Register a specific object instance (not a class/function).

    Used for data objects, scope entities, rules, etc. that are created
    at runtime rather than defined in code.
    """
    reg = registry or get_registry()
    return reg.register(
        kind=kind,
        namespace=namespace,
        name=name,
        registered_by=registered_by,
        target=instance,
        tags=tags or set(),
        metadata=metadata or {},
    )
