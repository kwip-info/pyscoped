"""Django adapter for the Scoped framework.

Add ``'scoped.contrib.django'`` to ``INSTALLED_APPS`` and the middleware
``'scoped.contrib.django.middleware.ScopedContextMiddleware'`` to
``MIDDLEWARE`` to enable automatic ScopedContext injection on every request.

After initialization, the simplified SDK (``scoped.objects``,
``scoped.principals``, etc.) works in every view.

Settings:
    SCOPED_BACKEND_USING:      Django DB alias (default ``"default"``)
    SCOPED_API_KEY:            Management plane API key (optional)
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
_client_instance = None


def get_backend():
    """Return the singleton DjangoORMBackend instance."""
    global _backend_instance
    if _backend_instance is None:
        from scoped.contrib.django.backend import DjangoORMBackend

        _backend_instance = DjangoORMBackend()
    return _backend_instance


def get_client():
    """Return the singleton ScopedClient backed by Django's database.

    Also sets the global default so ``scoped.objects``,
    ``scoped.principals``, etc. work in views.
    """
    global _client_instance
    if _client_instance is None:
        from django.conf import settings

        from scoped.client import init

        api_key = getattr(settings, "SCOPED_API_KEY", None)
        backend = get_backend()
        _client_instance = init(backend=backend, api_key=api_key)
    return _client_instance


def reset_backend():
    """Reset singletons (for tests)."""
    global _backend_instance, _client_instance
    _backend_instance = None
    _client_instance = None


def _initialize_backend():
    """Called from AppConfig.ready() — create Scoped tables and initialize client.

    Tolerates database errors during initial migration when tables
    don't exist yet. The backend will be initialized on first use.
    """
    try:
        backend = get_backend()
        backend.initialize()
        get_client()
    except Exception:
        pass


# Lazy imports for convenience — avoids circular import at module load time.
def __getattr__(name: str):
    if name == "ScopedModel":
        from scoped.contrib.django.models import ScopedModel
        return ScopedModel
    if name == "ScopedDjangoManager":
        from scoped.contrib.django.models import ScopedDjangoManager
        return ScopedDjangoManager
    if name == "scoped_context_for":
        from scoped.contrib.django.models import scoped_context_for
        return scoped_context_for
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
