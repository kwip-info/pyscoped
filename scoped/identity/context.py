"""ScopedContext — thread-safe 'who is acting right now' via contextvars.

Every framework operation reads the current context to determine the acting
principal.  If no context is set, ``NoContextError`` is raised.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from scoped.exceptions import NoContextError
from scoped.identity.principal import Principal

# The single context variable.  ``None`` means no principal is acting.
_current_context: ContextVar[ScopedContext | None] = ContextVar(
    "scoped_context", default=None
)


class ScopedContext:
    """
    Identifies *who* is acting for the duration of a block.

    Usage::

        with ScopedContext(principal=some_user):
            # everything in here is attributed to some_user
            ...

    Contexts nest — entering a new context pushes a frame that is popped
    on exit, restoring the prior context (or None).

    Attributes beyond ``principal`` (e.g. environment_id, scope_id) are
    stored in ``extras`` for downstream layers to consume.
    """

    def __init__(
        self,
        principal: Principal,
        **extras: Any,
    ) -> None:
        self.principal = principal
        self.extras: dict[str, Any] = extras
        self._token: Token[ScopedContext | None] | None = None

    # -- Context-manager protocol -------------------------------------------

    def __enter__(self) -> ScopedContext:
        self._token = _current_context.set(self)
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self._token is not None:
            _current_context.reset(self._token)
            self._token = None

    # -- Convenience accessors ----------------------------------------------

    @property
    def principal_id(self) -> str:
        return self.principal.id

    @property
    def principal_kind(self) -> str:
        return self.principal.kind

    # -- Class-level helpers ------------------------------------------------

    @classmethod
    def current(cls) -> ScopedContext:
        """Return the active context or raise ``NoContextError``."""
        ctx = _current_context.get()
        if ctx is None:
            raise NoContextError(
                "No ScopedContext is active — every operation requires a principal",
            )
        return ctx

    @classmethod
    def current_or_none(cls) -> ScopedContext | None:
        """Return the active context or ``None``."""
        return _current_context.get()

    @classmethod
    def current_principal(cls) -> Principal:
        """Shortcut: get the acting principal from the active context."""
        return cls.current().principal

    @classmethod
    def require(cls) -> ScopedContext:
        """Alias for ``current()`` — reads well at call sites."""
        return cls.current()
