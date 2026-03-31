"""Introspection: discover unregistered constructs and flag violations.

This module powers the compliance testing engine's registry completeness checks.
It can scan Python modules, Django apps, and arbitrary objects to find things
that should be registered but aren't.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

from scoped.registry.base import Registry, get_registry
from scoped.registry.kinds import RegistryKind


@dataclass(slots=True)
class IntrospectionResult:
    """Result of introspecting a module/package for unregistered constructs."""

    registered: list[str] = field(default_factory=list)      # names that are registered
    unregistered: list[str] = field(default_factory=list)    # names that should be registered
    skipped: list[str] = field(default_factory=list)         # names intentionally excluded

    @property
    def compliant(self) -> bool:
        return len(self.unregistered) == 0

    @property
    def coverage(self) -> float:
        total = len(self.registered) + len(self.unregistered)
        if total == 0:
            return 1.0
        return len(self.registered) / total


def _is_registerable(name: str, obj: Any) -> bool:
    """Determine if an object should be registered."""
    # Skip private/dunder
    if name.startswith("_"):
        return False

    # Skip imported standard library objects
    module = getattr(obj, "__module__", None)
    if module and (module.startswith("builtins") or module.startswith("typing")):
        return False

    # Classes and functions are registerable
    if inspect.isclass(obj) or inspect.isfunction(obj):
        return True

    return False


def _has_scoped_entry(obj: Any) -> bool:
    """Check if an object has been registered via @register or @track."""
    return hasattr(obj, "__scoped_entry__")


def introspect_module(
    module: ModuleType | str,
    *,
    registry: Registry | None = None,
    skip: set[str] | None = None,
) -> IntrospectionResult:
    """
    Scan a module for classes and functions, check which are registered.

    Args:
        module: A module object or dotted module path string.
        registry: Registry to check against (defaults to global).
        skip: Set of names to intentionally exclude from checking.
    """
    if isinstance(module, str):
        module = importlib.import_module(module)

    reg = registry or get_registry()
    skip_names = skip or set()
    result = IntrospectionResult()

    for name, obj in inspect.getmembers(module):
        if name in skip_names:
            result.skipped.append(name)
            continue

        if not _is_registerable(name, obj):
            continue

        # Check if the object's module matches (avoid counting re-exports)
        obj_module = getattr(obj, "__module__", None)
        if obj_module and obj_module != module.__name__:
            continue

        if _has_scoped_entry(obj) or reg.contains_target(obj):
            result.registered.append(name)
        else:
            result.unregistered.append(name)

    return result


def introspect_package(
    package: str,
    *,
    registry: Registry | None = None,
    skip: set[str] | None = None,
    recursive: bool = True,
) -> dict[str, IntrospectionResult]:
    """
    Scan an entire package for unregistered constructs.

    Returns a dict of module_name -> IntrospectionResult.
    """
    pkg = importlib.import_module(package)
    results: dict[str, IntrospectionResult] = {}

    results[package] = introspect_module(pkg, registry=registry, skip=skip)

    if recursive and hasattr(pkg, "__path__"):
        for importer, modname, ispkg in pkgutil.walk_packages(
            pkg.__path__, prefix=package + "."
        ):
            try:
                mod = importlib.import_module(modname)
                results[modname] = introspect_module(mod, registry=registry, skip=skip)
            except ImportError:
                pass

    return results
