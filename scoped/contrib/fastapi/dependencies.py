"""FastAPI dependency injection for Scoped."""

from __future__ import annotations

from fastapi import HTTPException, Request


def get_backend():
    """Dependency: returns the global StorageBackend."""
    from scoped.contrib.fastapi import _get_backend

    return _get_backend()


def get_scoped_context(request: Request):
    """Dependency: returns the ScopedContext set by middleware."""
    ctx = getattr(request.state, "scoped_context", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="No Scoped principal context")
    return ctx


def get_principal(request: Request):
    """Dependency: returns the acting Principal from the request context."""
    ctx = getattr(request.state, "scoped_context", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="No Scoped principal context")
    return ctx.principal


def get_services():
    """Dependency: returns the full Scoped service dict."""
    from scoped.contrib._base import build_services
    from scoped.contrib.fastapi import _get_backend

    return build_services(_get_backend())
