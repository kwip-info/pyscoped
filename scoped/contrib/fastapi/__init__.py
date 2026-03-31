"""FastAPI adapter for the Scoped framework.

Provides middleware, dependency injection, Pydantic schemas, and
pre-built admin routes for integrating Scoped into a FastAPI application.

Usage::

    from fastapi import FastAPI
    from scoped.contrib.fastapi.middleware import ScopedContextMiddleware

    app = FastAPI()
    app.add_middleware(
        ScopedContextMiddleware,
        database_url="postgresql://user:pass@host/db",
    )

    # scoped.objects, scoped.principals, etc. now work in route handlers
"""

from __future__ import annotations

try:
    import fastapi  # noqa: F401
except ImportError as exc:
    raise ImportError(
        "scoped.contrib.fastapi requires FastAPI. "
        "Install with: pip install scoped[fastapi]"
    ) from exc
