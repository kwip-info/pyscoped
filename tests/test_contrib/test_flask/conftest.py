"""Flask adapter test fixtures."""

from __future__ import annotations

import pytest

flask = pytest.importorskip("flask")

from flask import Flask

from scoped.contrib.flask.admin import admin_bp
from scoped.contrib.flask.extension import ScopedExtension
from scoped.identity.context import ScopedContext
from scoped.identity.principal import PrincipalStore


@pytest.fixture
def flask_app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SCOPED_EXEMPT_PATHS"] = ["/exempt/"]

    scoped = ScopedExtension(app)
    app.register_blueprint(admin_bp)

    @app.route("/test")
    def test_view():
        from flask import g, jsonify

        ctx = getattr(g, "scoped_context", None)
        if ctx:
            return jsonify({"principal_id": ctx.principal_id})
        return jsonify({"principal_id": None})

    @app.route("/exempt/test")
    def exempt_view():
        from flask import jsonify

        return jsonify({"exempt": True})

    yield {"app": app, "ext": scoped}


@pytest.fixture
def flask_user(flask_app):
    backend = flask_app["ext"].backend
    store = PrincipalStore(backend)
    return store.create_principal(kind="user", display_name="Flask User")


@pytest.fixture
def flask_client(flask_app):
    return flask_app["app"].test_client()
