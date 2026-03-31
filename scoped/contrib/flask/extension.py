"""Flask extension for Scoped — ``init_app`` pattern."""

from __future__ import annotations

from typing import Any, Callable


class ScopedExtension:
    """Flask extension that injects ``ScopedContext`` per request.

    Usage::

        scoped = ScopedExtension()
        scoped.init_app(app)

    Or::

        scoped = ScopedExtension(app)

    After initialisation:
    - ``g.scoped_context`` holds the active ``ScopedContext`` (or ``None``)
    - ``current_app.extensions["scoped"]`` provides access to backend/services
    """

    def __init__(self, app=None) -> None:
        self._backend = None
        self._services: dict[str, Any] | None = None
        if app is not None:
            self.init_app(app)

    def init_app(self, app) -> None:
        app.extensions["scoped"] = self

        backend_type = app.config.get("SCOPED_STORAGE_BACKEND", "sqlite")
        if backend_type == "sqlite":
            from scoped.storage.sqlite import SQLiteBackend

            path = app.config.get("SCOPED_SQLITE_PATH", ":memory:")
            self._backend = SQLiteBackend(path)
            self._backend.initialize()
        elif backend_type == "postgres":
            from scoped.storage.postgres import PostgresBackend

            dsn = app.config.get("SCOPED_POSTGRES_DSN", "")
            opts = app.config.get("SCOPED_POSTGRES_OPTIONS", {})
            self._backend = PostgresBackend(dsn, **opts)
            self._backend.initialize()

        from scoped.contrib._base import build_services

        self._services = build_services(self._backend)

        app.before_request(self._before_request)
        app.teardown_request(self._teardown_request)

    @property
    def backend(self):
        return self._backend

    @property
    def services(self) -> dict[str, Any]:
        return self._services or {}

    def _before_request(self) -> None:
        from flask import g, request

        exempt = request.app.config.get("SCOPED_EXEMPT_PATHS", []) if hasattr(request, "app") else []
        if not exempt:
            from flask import current_app
            exempt = current_app.config.get("SCOPED_EXEMPT_PATHS", [])

        if any(request.path.startswith(p) for p in exempt):
            g.scoped_context = None
            return

        principal = self._resolve_principal()
        if principal is None:
            g.scoped_context = None
            return

        from scoped.identity.context import ScopedContext

        ctx = ScopedContext(principal=principal)
        ctx.__enter__()
        g.scoped_context = ctx

    def _teardown_request(self, exc) -> None:
        from flask import g

        ctx = getattr(g, "scoped_context", None)
        if ctx is not None:
            ctx.__exit__(None, None, None)
            g.scoped_context = None

    def _resolve_principal(self):
        from flask import current_app, request

        resolver = current_app.config.get("SCOPED_PRINCIPAL_RESOLVER")
        if resolver:
            return resolver(request)

        header = current_app.config.get("SCOPED_PRINCIPAL_HEADER", "X-Scoped-Principal-Id")
        pid = request.headers.get(header)
        if pid and self._backend:
            from scoped.contrib._base import resolve_principal_from_id

            return resolve_principal_from_id(self._backend, pid)
        return None
