"""FastAPI dependency injection for Scoped.

These dependencies work after ``ScopedContextMiddleware`` has been
added to the FastAPI app.

Usage::

    from fastapi import Depends
    from scoped.contrib.fastapi.dependencies import get_principal, get_client

    @app.get("/me")
    def whoami(principal=Depends(get_principal)):
        return {"id": principal.id, "kind": principal.kind}
"""

from __future__ import annotations

from fastapi import HTTPException, Request


def get_backend():
    """Dependency: returns the StorageBackend from the global client."""
    from scoped.client import _get_default_client

    return _get_default_client().backend


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


def get_client():
    """Dependency: returns the global ``ScopedClient``.

    Use this when you need the full client in a route handler::

        @app.get("/audit")
        def audit(client=Depends(get_client)):
            return client.audit.for_principal("user-123")
    """
    from scoped.client import _get_default_client

    return _get_default_client()


def get_services():
    """Dependency: returns the raw Scoped service dict.

    .. deprecated::
        Use ``get_client()`` instead for the simplified API.
        This function is kept for backward compatibility.
    """
    from scoped.contrib._base import build_services
    from scoped.client import _get_default_client

    return build_services(_get_default_client().backend)
