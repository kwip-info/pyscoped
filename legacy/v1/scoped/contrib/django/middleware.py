"""Django middleware for ScopedContext injection.

Resolves the acting principal from the request and wraps the handler
in a ``ScopedContext`` so all operations within the request are
attributed to the correct actor.

Supports both sync and async Django views (Django 4.1+).
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable

from django.http import HttpRequest, HttpResponse
from django.utils.decorators import sync_and_async_middleware


@sync_and_async_middleware
class ScopedContextMiddleware:
    """Inject a ``ScopedContext`` for every request.

    Principal resolution order:
    1. Custom resolver (``SCOPED_PRINCIPAL_RESOLVER`` setting)
    2. Header-based (``SCOPED_PRINCIPAL_HEADER`` setting, default
       ``HTTP_X_SCOPED_PRINCIPAL_ID``)

    Paths listed in ``SCOPED_EXEMPT_PATHS`` are skipped.

    Supports both sync and async Django views via ``sync_and_async_middleware``.
    """

    def __init__(self, get_response: Callable) -> None:
        self.get_response = get_response
        self._backend = None
        self._resolver: Callable | None = None
        self._resolved = False
        self.async_mode = asyncio.iscoroutinefunction(get_response)

    def _get_backend(self):
        if self._backend is None:
            from scoped.contrib.django import get_backend

            self._backend = get_backend()
        return self._backend

    def _get_resolver(self):
        if not self._resolved:
            from django.conf import settings

            custom = getattr(settings, "SCOPED_PRINCIPAL_RESOLVER", None)
            if custom:
                if isinstance(custom, str):
                    from django.utils.module_loading import import_string

                    self._resolver = import_string(custom)
                else:
                    self._resolver = custom
            self._resolved = True
        return self._resolver

    def _is_exempt(self, request: HttpRequest) -> bool:
        from django.conf import settings

        exempt = getattr(settings, "SCOPED_EXEMPT_PATHS", [])
        return any(request.path.startswith(p) for p in exempt)

    # -- Sync path ---------------------------------------------------------

    def _handle_scoped_request(self, request: HttpRequest) -> HttpResponse:
        """Resolve principal and wrap in transaction + context."""
        from django.db import transaction

        with transaction.atomic():
            return self._resolve_and_dispatch(request)

    def _resolve_and_dispatch(self, request: HttpRequest) -> HttpResponse:
        """Resolve principal, set context, dispatch to view."""
        principal = self._resolve_principal(request)
        if principal is None:
            return self.get_response(request)

        from scoped.identity.context import ScopedContext

        ctx = ScopedContext(principal=principal)
        ctx.__enter__()
        try:
            request.scoped_context = ctx  # type: ignore[attr-defined]
            response = self.get_response(request)
        finally:
            ctx.__exit__(None, None, None)
        return response

    def _resolve_principal(self, request: HttpRequest):
        resolver = self._get_resolver()
        if resolver:
            return resolver(request)

        from django.conf import settings

        header = getattr(settings, "SCOPED_PRINCIPAL_HEADER", "HTTP_X_SCOPED_PRINCIPAL_ID")
        principal_id = request.META.get(header)
        if principal_id:
            from scoped.contrib.django import get_client

            return get_client().principals.find(principal_id)
        return None

    # -- Async path --------------------------------------------------------

    async def _handle_scoped_request_async(self, request: HttpRequest) -> HttpResponse:
        """Async version — resolves principal and sets context."""
        principal = await self._resolve_principal_async(request)
        if principal is None:
            return await self.get_response(request)

        from scoped.identity.context import ScopedContext

        ctx = ScopedContext(principal=principal)
        ctx.__enter__()
        try:
            request.scoped_context = ctx  # type: ignore[attr-defined]
            response = await self.get_response(request)
        finally:
            ctx.__exit__(None, None, None)
        return response

    async def _resolve_principal_async(self, request: HttpRequest):
        resolver = self._get_resolver()
        if resolver:
            result = resolver(request)
            if inspect.isawaitable(result):
                return await result
            return result

        from django.conf import settings

        header = getattr(settings, "SCOPED_PRINCIPAL_HEADER", "HTTP_X_SCOPED_PRINCIPAL_ID")
        principal_id = request.META.get(header)
        if principal_id:
            from scoped.contrib.django import get_client

            return get_client().principals.find(principal_id)
        return None

    # -- Dispatch ----------------------------------------------------------

    def __call__(self, request: HttpRequest):
        if self._is_exempt(request):
            if self.async_mode:
                return self.get_response(request)
            return self.get_response(request)

        if self.async_mode:
            return self._handle_scoped_request_async(request)
        return self._handle_scoped_request(request)
