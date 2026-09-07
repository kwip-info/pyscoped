"""Shared helpers for namespace proxy classes.

Provides context-aware principal resolution and flexible ID extraction
used by every namespace. These are internal utilities — end users
interact with them indirectly through namespace methods.

Context resolution order:
    1. Explicit value passed by the caller (always wins)
    2. Active ``ScopedContext`` principal (set via ``with client.as_principal(...)``)
    3. Raise ``RuntimeError`` (or return ``None`` for optional actors)
"""

from __future__ import annotations

from typing import Any


def _resolve_principal_id(explicit: str | None = None) -> str:
    """Return the acting principal ID.

    Checks in order:
        1. *explicit* — if the caller passed a value, use it.
        2. ``ScopedContext.current()`` — if a context block is active,
           use its ``principal_id``.
        3. Raise ``RuntimeError`` — no principal available.

    Args:
        explicit: An explicitly provided principal ID. Takes precedence
                  over the context.

    Returns:
        The resolved principal ID string.

    Raises:
        RuntimeError: If no explicit ID was given and no ``ScopedContext``
                      is active.

    Example::

        # Inside a context block, principal_id is inferred:
        with client.as_principal(alice):
            pid = _resolve_principal_id()  # returns alice.id

        # Explicit value always wins:
        pid = _resolve_principal_id("bob-123")  # returns "bob-123"
    """
    if explicit is not None:
        return explicit

    from scoped.identity.context import ScopedContext

    ctx = ScopedContext.current_or_none()
    if ctx is not None:
        return ctx.principal_id

    raise RuntimeError(
        "No principal specified and no ScopedContext is active. "
        "Either pass principal_id explicitly or use "
        "'with client.as_principal(user):' to set the acting principal."
    )


def _try_resolve_principal_id() -> str | None:
    """Return the acting principal ID, or ``None`` if unavailable.

    Unlike ``_resolve_principal_id``, this never raises. Use it for
    optional actor fields like ``created_by`` where ``"system"`` is an
    acceptable fallback.

    Returns:
        The principal ID from the active context, or ``None``.
    """
    from scoped.identity.context import ScopedContext

    ctx = ScopedContext.current_or_none()
    return ctx.principal_id if ctx is not None else None


def _to_id(obj_or_id: Any) -> str:
    """Extract an ID string from a model object or pass through a string.

    Accepts:
        - A string — returned as-is.
        - Any object with an ``.id`` attribute (``Principal``, ``Scope``,
          ``ScopedObject``, etc.) — returns ``obj.id``.

    Args:
        obj_or_id: A string ID or a model object with an ``.id`` attribute.

    Returns:
        The ID string.

    Raises:
        TypeError: If the argument is neither a string nor has an ``.id``
                   attribute.

    Example::

        _to_id("abc-123")      # "abc-123"
        _to_id(alice)           # alice.id
        _to_id(some_scope)      # some_scope.id
    """
    if isinstance(obj_or_id, str):
        return obj_or_id
    if hasattr(obj_or_id, "id"):
        return obj_or_id.id
    raise TypeError(
        f"Expected a string ID or an object with an .id attribute, "
        f"got {type(obj_or_id).__name__}"
    )
