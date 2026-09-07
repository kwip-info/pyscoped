from django.core.exceptions import PermissionDenied


class ScopedError(Exception):
    """Base for PyScoped integration errors."""


class MissingContext(PermissionDenied, ScopedError):
    """An operation requires an explicitly attributed scope context."""


class ScopeViolation(PermissionDenied, ScopedError):
    """An operation crosses its authorized scope."""


class UnsupportedOperation(ScopedError):
    """An operation cannot preserve the registered guarantees."""


class HistoryConflict(ScopedError):
    """The current row or history has changed since the caller inspected it."""
