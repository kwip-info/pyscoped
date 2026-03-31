"""FastAPI/Starlette middleware for ScopedContext injection."""

from __future__ import annotations

from typing import Any, Callable, Awaitable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class ScopedContextMiddleware(BaseHTTPMiddleware):
    """Wrap each request in a ``ScopedContext``.

    Args:
        app: The ASGI application.
        backend: StorageBackend instance.
        principal_header: Header name containing the principal ID.
        principal_resolver: Optional callable(request) -> Principal | None.
        exempt_paths: Path prefixes to skip.
    """

    def __init__(
        self,
        app,
        *,
        backend=None,
        principal_header: str = "x-scoped-principal-id",
        principal_resolver: Callable[[Request], Any] | None = None,
        exempt_paths: list[str] | None = None,
    ):
        super().__init__(app)
        self.backend = backend
        self.principal_header = principal_header
        self.principal_resolver = principal_resolver
        self.exempt_paths = exempt_paths or []

        # Register backend globally for dependency injection
        if backend is not None:
            from scoped.contrib.fastapi import set_backend

            set_backend(backend)

    async def dispatch(self, request: Request, call_next) -> Response:
        if any(request.url.path.startswith(p) for p in self.exempt_paths):
            return await call_next(request)

        principal = None
        if self.principal_resolver:
            result = self.principal_resolver(request)
            # Support both sync and async resolvers
            if hasattr(result, "__await__"):
                principal = await result
            else:
                principal = result
        elif self.backend:
            pid = request.headers.get(self.principal_header)
            if pid:
                from scoped.contrib._base import resolve_principal_from_id

                principal = resolve_principal_from_id(self.backend, pid)

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
