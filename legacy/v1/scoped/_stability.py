"""Stability markers for pyscoped APIs.

Provides decorators to mark classes and functions as stable, experimental,
or preview.  Experimental and preview APIs emit a warning on first use so
developers know the API may change.

Warning categories
------------------
- ``ExperimentalAPIWarning`` — API may change without notice.
- ``PreviewAPIWarning`` — API is near-final but not yet committed.

Both inherit from ``FutureWarning`` so they are shown by default in all
Python environments.  Users can suppress with::

    import warnings
    warnings.filterwarnings("ignore", category=ExperimentalAPIWarning)

Usage
-----
::

    from scoped._stability import experimental, preview, stable

    @experimental("Container API subject to change")
    class EnvironmentContainer:
        ...

    @preview()
    def sync_connector(connector_id):
        ...

    @stable(since="0.6.0")
    class ScopedManager:
        ...
"""

from __future__ import annotations

import enum
import functools
import warnings
from typing import Any, Callable, TypeVar, Union

__all__ = [
    "StabilityLevel",
    "ExperimentalAPIWarning",
    "PreviewAPIWarning",
    "experimental",
    "preview",
    "stable",
    "get_stability_level",
]

# ---------------------------------------------------------------------------
# Warning classes
# ---------------------------------------------------------------------------


class ExperimentalAPIWarning(FutureWarning):
    """Emitted when an experimental pyscoped API is used for the first time."""


class PreviewAPIWarning(FutureWarning):
    """Emitted when a preview pyscoped API is used for the first time."""


# ---------------------------------------------------------------------------
# Stability level enum
# ---------------------------------------------------------------------------


class StabilityLevel(enum.Enum):
    STABLE = "stable"
    EXPERIMENTAL = "experimental"
    PREVIEW = "preview"


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_STABILITY_ATTR = "__scoped_stability__"
_WARNED: set[str] = set()  # tracks "module.qualname" to warn once per symbol

F = TypeVar("F", bound=Callable)


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------


def experimental(reason: str = "") -> Callable:
    """Mark a class or function as experimental.

    Experimental APIs may change or be removed in any release.
    A ``ExperimentalAPIWarning`` is emitted on first use.
    """

    def decorator(obj: Any) -> Any:
        setattr(obj, _STABILITY_ATTR, StabilityLevel.EXPERIMENTAL)
        if isinstance(obj, type):
            return _wrap_class(obj, StabilityLevel.EXPERIMENTAL, reason)
        return _wrap_function(obj, StabilityLevel.EXPERIMENTAL, reason)

    return decorator


def preview(reason: str = "") -> Callable:
    """Mark a class or function as preview.

    Preview APIs are near-final but may still change before stabilisation.
    A ``PreviewAPIWarning`` is emitted on first use.
    """

    def decorator(obj: Any) -> Any:
        setattr(obj, _STABILITY_ATTR, StabilityLevel.PREVIEW)
        if isinstance(obj, type):
            return _wrap_class(obj, StabilityLevel.PREVIEW, reason)
        return _wrap_function(obj, StabilityLevel.PREVIEW, reason)

    return decorator


def stable(since: str = "") -> Callable:
    """Mark a class or function as stable.

    Stable APIs will not have breaking changes without a major version bump.
    No warning is emitted.
    """

    def decorator(obj: Any) -> Any:
        setattr(obj, _STABILITY_ATTR, StabilityLevel.STABLE)
        if since:
            obj._stability_since = since
        return obj

    return decorator


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def get_stability_level(obj: Any) -> StabilityLevel | None:
    """Return the stability level of a decorated object, or ``None``."""
    return getattr(obj, _STABILITY_ATTR, None)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_WARNING_CLS = {
    StabilityLevel.EXPERIMENTAL: ExperimentalAPIWarning,
    StabilityLevel.PREVIEW: PreviewAPIWarning,
}


def _build_message(qualname: str, level: StabilityLevel, reason: str) -> str:
    msg = f"{qualname} is {level.value}"
    if reason:
        msg += f" ({reason})"
    msg += ". API may change in future releases."
    return msg


def _wrap_class(cls: type, level: StabilityLevel, reason: str) -> type:
    """Wrap ``__init__`` to emit a warning on first instantiation."""
    original_init = cls.__init__
    key = f"{cls.__module__}.{cls.__qualname__}"
    msg = _build_message(cls.__qualname__, level, reason)
    warning_cls = _WARNING_CLS[level]

    @functools.wraps(original_init)
    def warned_init(self: Any, *args: Any, **kwargs: Any) -> None:
        if key not in _WARNED:
            _WARNED.add(key)
            warnings.warn(msg, warning_cls, stacklevel=2)
        original_init(self, *args, **kwargs)

    cls.__init__ = warned_init  # type: ignore[attr-defined]
    return cls


def _wrap_function(func: F, level: StabilityLevel, reason: str) -> F:
    """Wrap a function to emit a warning on first call."""
    key = f"{func.__module__}.{func.__qualname__}"
    msg = _build_message(func.__qualname__, level, reason)
    warning_cls = _WARNING_CLS[level]

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if key not in _WARNED:
            _WARNED.add(key)
            warnings.warn(msg, warning_cls, stacklevel=2)
        return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]
