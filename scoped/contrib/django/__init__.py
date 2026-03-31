"""Django adapter for the Scoped framework.

Add ``'scoped.contrib.django'`` to ``INSTALLED_APPS`` and the middleware
``'scoped.contrib.django.middleware.ScopedContextMiddleware'`` to
``MIDDLEWARE`` to enable automatic ScopedContext injection on every request.

Settings:
    SCOPED_BACKEND_USING:      Django DB alias (default ``"default"``)
    SCOPED_PRINCIPAL_HEADER:   HTTP header for principal ID
                               (default ``"HTTP_X_SCOPED_PRINCIPAL_ID"``)
    SCOPED_PRINCIPAL_RESOLVER: Dotted path to a callable(request) -> Principal | None
    SCOPED_EXEMPT_PATHS:       List of path prefixes to skip (default ``[]``)
"""

from __future__ import annotations

try:
    import django  # noqa: F401
except ImportError as exc:
    raise ImportError(
        "scoped.contrib.django requires Django. "
        "Install with: pip install scoped[django]"
    ) from exc

_backend_instance = None


def get_backend():
    """Return the singleton DjangoORMBackend instance."""
    global _backend_instance
    if _backend_instance is None:
        from scoped.contrib.django.backend import DjangoORMBackend

        _backend_instance = DjangoORMBackend()
    return _backend_instance


def reset_backend():
    """Reset the singleton (for tests)."""
    global _backend_instance
    _backend_instance = None


def _initialize_backend():
    """Called from AppConfig.ready() — create Scoped tables if needed."""
    backend = get_backend()
    backend.initialize()
