"""FastAPI/Starlette middleware for ScopedContext injection.

Integrates pyscoped into FastAPI with a single middleware. After adding
the middleware, the simplified SDK (``scoped.objects``,
``scoped.principals``, etc.) works in every route handler.

Usage::

    from fastapi import FastAPI
    from scoped.contrib.fastapi.middleware import ScopedContextMiddleware

    app = FastAPI()
    app.add_middleware(
        ScopedContextMiddleware,
        database_url="postgresql://user:pass@host/db",
        api_key="psc_live_...",  # optional
    )

    @app.post("/invoices")
    def create_invoice():
        import scoped
        doc, v1 = scoped.objects.create("invoice", data={"amount": 100})
        return {"id": doc.id}

Configuration:
    ``database_url``         Database URL (``sqlite:///``, ``postgresql://``)
    ``api_key``              Management plane API key (optional)
    ``principal_header``     HTTP header for principal ID
                             (default ``x-scoped-principal-id``)
    ``principal_resolver``   Callable(request) -> Principal | None
    ``exempt_paths``         List of path prefixes to skip
"""

from __future__ import annotations

from typing import Any, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class ScopedContextMiddleware(BaseHTTPMiddleware):
    """Wrap each request in a ``ScopedContext``.

    Initializes a ``ScopedClient`` on first use and sets it as the
    global default so ``scoped.objects``, ``scoped.principals``, etc.
    work in route handlers.

    Args:
        app: The ASGI application.
        database_url: Database URL for pyscoped. Defaults to in-memory SQLite.
        api_key: Management plane API key (optional).
        backend: Pre-built ``StorageBackend`` (overrides ``database_url``).
        principal_header: Header name containing the principal ID.
        principal_resolver: Optional callable(request) -> Principal | None.
        exempt_paths: Path prefixes to skip.
    """

    def __init__(
        self,
        app,
        *,
        database_url: str | None = None,
        api_key: str | None = None,
        backend: Any | None = None,
        principal_header: str = "x-scoped-principal-id",
        principal_resolver: Callable[[Request], Any] | None = None,
        exempt_paths: list[str] | None = None,
    ):
        super().__init__(app)
        self.principal_header = principal_header
        self.principal_resolver = principal_resolver
        self.exempt_paths = exempt_paths or []

        from scoped.client import init

        self._client = init(
            database_url=database_url,
            api_key=api_key,
            backend=backend,
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        if any(request.url.path.startswith(p) for p in self.exempt_paths):
            return await call_next(request)

        principal = await self._resolve_principal(request)

        if principal is None:
            return await call_next(request)

        from scoped.identity.context import ScopedContext

        ctx = ScopedContext(principal=principal)
        ctx.__enter__()
        try:
            request.state.scoped_context = ctx
            response = await call_next(request)
        finally:
            ctx.__exit__(None, None, None)
        return response

    async def _resolve_principal(self, request: Request):
        if self.principal_resolver:
            result = self.principal_resolver(request)
            if hasattr(result, "__await__"):
                return await result
            return result

        pid = request.headers.get(self.principal_header)
        if pid:
            return self._client.principals.find(pid)
        return None
