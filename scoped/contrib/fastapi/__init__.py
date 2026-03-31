"""FastAPI adapter for the Scoped framework.

Provides middleware, dependency injection, Pydantic schemas, and
pre-built admin routes for integrating Scoped into a FastAPI application.

Usage::

    from fastapi import FastAPI
    from scoped.contrib.fastapi.middleware import ScopedContextMiddleware
    from scoped.contrib.fastapi.router import router as scoped_router

    app = FastAPI()
    app.add_middleware(ScopedContextMiddleware, backend=my_backend)
    app.include_router(scoped_router)
"""

from __future__ import annotations

try:
    import fastapi  # noqa: F401
except ImportError as exc:
    raise ImportError(
        "scoped.contrib.fastapi requires FastAPI. "
        "Install with: pip install scoped[fastapi]"
    ) from exc

_backend_instance = None


def _get_backend():
    """Return the module-level backend (set via ``set_backend``)."""
    global _backend_instance
    if _backend_instance is None:
        raise RuntimeError(
            "No Scoped backend configured. Call set_backend() or use "
            "ScopedContextMiddleware with a backend= argument."
        )
    return _backend_instance


def set_backend(backend):
    """Set the module-level storage backend for dependency injection."""
    global _backend_instance
    _backend_instance = backend


def reset_backend():
    """Reset the singleton (for tests)."""
    global _backend_instance
    _backend_instance = None
