"""Flask extension for Scoped — ``init_app`` pattern.

Integrates pyscoped into Flask with a single extension. After
initialization, the simplified SDK (``scoped.objects``,
``scoped.principals``, etc.) works in every request handler.

Usage::

    from flask import Flask
    from scoped.contrib.flask import ScopedExtension

    app = Flask(__name__)
    app.config["SCOPED_DATABASE_URL"] = "postgresql://user:pass@host/db"
    app.config["SCOPED_API_KEY"] = "psc_live_..."  # optional

    ScopedExtension(app)

    @app.route("/invoices", methods=["POST"])
    def create_invoice():
        # ScopedContext is set per-request — scoped.objects just works
        import scoped
        doc, v1 = scoped.objects.create("invoice", data=request.json)
        return {"id": doc.id}

Configuration keys:
    ``SCOPED_DATABASE_URL``      Database URL (``sqlite:///``, ``postgresql://``)
    ``SCOPED_API_KEY``           Management plane API key (optional)
    ``SCOPED_PRINCIPAL_HEADER``  HTTP header for principal ID
                                 (default ``X-Scoped-Principal-Id``)
    ``SCOPED_PRINCIPAL_RESOLVER`` Callable(request) -> Principal | None
    ``SCOPED_EXEMPT_PATHS``      List of path prefixes to skip
"""

from __future__ import annotations

from typing import Any, Callable


class ScopedExtension:
    """Flask extension that initializes pyscoped and injects
    ``ScopedContext`` per request.

    After ``init_app()``:
    - ``scoped.objects``, ``scoped.principals``, etc. work in route handlers
    - ``g.scoped_context`` holds the active context (or ``None``)
    - ``current_app.extensions["scoped"]`` provides the ``ScopedClient``
    """

    def __init__(self, app=None) -> None:
        self._client = None
        if app is not None:
            self.init_app(app)

    def init_app(self, app) -> None:
        """Initialize pyscoped for this Flask app.

        Reads ``SCOPED_DATABASE_URL`` and ``SCOPED_API_KEY`` from
        ``app.config``, creates a ``ScopedClient``, and registers
        request hooks for automatic ``ScopedContext`` injection.
        """
        from scoped.client import init

        database_url = app.config.get("SCOPED_DATABASE_URL")
        api_key = app.config.get("SCOPED_API_KEY")

        self._client = init(database_url=database_url, api_key=api_key)
        app.extensions["scoped"] = self

        app.before_request(self._before_request)
        app.teardown_request(self._teardown_request)

    @property
    def client(self):
        """The ``ScopedClient`` instance."""
        return self._client

    @property
    def backend(self):
        """The storage backend (shortcut for ``client.backend``)."""
        return self._client.backend if self._client else None

    @property
    def services(self) -> dict:
        """Service dict for backward compatibility.

        Prefer using ``client`` directly for the simplified API.
        """
        if self._client is None:
            return {}
        from scoped.contrib._base import build_services

        return build_services(self._client.backend)

    def _before_request(self) -> None:
        from flask import g, request

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
        if pid and self._client:
            return self._client.principals.find(pid)
        return None
