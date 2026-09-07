"""Flask adapter for the Scoped framework.

Usage::

    from flask import Flask
    from scoped.contrib.flask.extension import ScopedExtension
    from scoped.contrib.flask.admin import admin_bp

    app = Flask(__name__)
    scoped = ScopedExtension(app)
    app.register_blueprint(admin_bp)

Configuration keys (``app.config``):
    SCOPED_STORAGE_BACKEND:    ``"sqlite"`` (default)
    SCOPED_SQLITE_PATH:        Database path (default ``":memory:"``)
    SCOPED_PRINCIPAL_HEADER:   HTTP header for principal ID
    SCOPED_PRINCIPAL_RESOLVER: Callable(request) -> Principal | None
    SCOPED_EXEMPT_PATHS:       List of path prefixes to skip
"""

from __future__ import annotations

try:
    import flask  # noqa: F401
except ImportError as exc:
    raise ImportError(
        "scoped.contrib.flask requires Flask. "
        "Install with: pip install scoped[flask]"
    ) from exc
