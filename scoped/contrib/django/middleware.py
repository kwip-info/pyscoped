"""Django middleware for ScopedContext injection.

Resolves the acting principal from the request and wraps the handler
in a ``ScopedContext`` so all operations within the request are
attributed to the correct actor.
"""

from __future__ import annotations

from typing import Any, Callable

from django.http import HttpRequest, HttpResponse


class ScopedContextMiddleware:
    """Inject a ``ScopedContext`` for every request.

    Principal resolution order:
    1. Custom resolver (``SCOPED_PRINCIPAL_RESOLVER`` setting)
    2. Header-based (``SCOPED_PRINCIPAL_HEADER`` setting, default
       ``HTTP_X_SCOPED_PRINCIPAL_ID``)

    Paths listed in ``SCOPED_EXEMPT_PATHS`` are skipped.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self._backend = None
        self._resolver: Callable | None = None
        self._resolved = False

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

    def _resolve_principal(self, request: HttpRequest):
        resolver = self._get_resolver()
        if resolver:
            return resolver(request)

        from django.conf import settings

        header = getattr(settings, "SCOPED_PRINCIPAL_HEADER", "HTTP_X_SCOPED_PRINCIPAL_ID")
        principal_id = request.META.get(header)
        if principal_id:
            from scoped.contrib._base import resolve_principal_from_id

            return resolve_principal_from_id(self._get_backend(), principal_id)
        return None

    def __call__(self, request: HttpRequest) -> HttpResponse:
        from django.conf import settings

        exempt = getattr(settings, "SCOPED_EXEMPT_PATHS", [])
        if any(request.path.startswith(p) for p in exempt):
            return self.get_response(request)

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
