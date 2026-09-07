"""Trusted application context; this module does not authenticate or authorize users."""

from contextlib import ContextDecorator, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from inspect import isasyncgenfunction, iscoroutinefunction, isgeneratorfunction

from .exceptions import MissingContext, UnsupportedOperation


def identity(value):
    if hasattr(value, "pk"):
        if getattr(value, "is_authenticated", True) is False:
            raise ValueError("Anonymous users cannot be actors.")
        value = value.pk
    if value is None or isinstance(value, bool):
        raise ValueError("An explicit, nonempty identity is required.")
    result = str(value)
    if not result.strip() or len(result) > 255:
        raise ValueError("Identity must contain 1–255 characters.")
    return result


@dataclass(frozen=True)
class ScopeContext:
    actor: str
    scope: str
    reason: str = ""
    request_id: str = ""

    def __post_init__(self):
        object.__setattr__(self, "actor", identity(self.actor))
        object.__setattr__(self, "scope", identity(self.scope))
        if not isinstance(self.reason, str) or not isinstance(self.request_id, str):
            raise ValueError("reason and request_id must be strings.")
        if len(self.request_id) > 255:
            raise ValueError("request_id must be at most 255 characters.")


_current = ContextVar("pyscoped_context", default=None)


@contextmanager
def _bind(ctx):
    """Re-enter the exact authorized context across streaming chunks."""
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


def current_context(*, required=True):
    ctx = _current.get()
    if ctx is None and required:
        raise MissingContext(
            "Use pyscoped.scope(actor=..., scope=...) after authorizing membership."
        )
    return ctx


class scope(ContextDecorator):
    """Sync/async context manager. Always restores the enclosing context."""

    def __init__(self, *, actor, scope, reason="", request_id=""):
        self.context = ScopeContext(actor, scope, reason, request_id)
        self._token = None

    def _recreate_cm(self):
        return type(self)(**self.context.__dict__)

    def __call__(self, function):
        if isgeneratorfunction(function) or isasyncgenfunction(function):
            raise UnsupportedOperation(
                "Use scope inside generators, or ScopedMiddleware streaming."
            )
        if iscoroutinefunction(function):

            @wraps(function)
            async def wrapped(*args, **kwargs):
                async with self._recreate_cm():
                    return await function(*args, **kwargs)

            return wrapped
        return super().__call__(function)

    def __enter__(self):
        if self._token is not None:
            raise RuntimeError("Use a fresh scope manager for each nested/concurrent entry.")
        self._token = _current.set(self.context)
        return self.context

    def __exit__(self, *exc):
        _current.reset(self._token)
        self._token = None

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *exc):
        self.__exit__(*exc)
