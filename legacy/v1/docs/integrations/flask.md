---
title: Flask Integration
description: Integrate pyscoped with Flask using the Scoped extension for automatic context management, principal resolution, and a built-in admin health blueprint.
category: integrations
---

# Flask Integration

pyscoped provides a Flask extension that handles backend initialization,
per-request scoped context management, and an admin blueprint for health
checks.

## Installation

```bash
pip install pyscoped[flask]
```

This installs pyscoped along with Flask-specific dependencies.

## Quick Start

```python
from flask import Flask
from scoped.contrib.flask import Scoped

app = Flask(__name__)
app.config["SCOPED_DATABASE_URL"] = "postgresql://localhost/mydb"
app.config["SCOPED_API_KEY"] = "sk-scoped-..."

scoped_ext = Scoped(app)
```

## Extension Setup

### Direct Initialization

Pass the Flask application directly to the `Scoped` constructor. The
extension configures the backend and registers `before_request` /
`teardown_request` hooks immediately.

```python
from flask import Flask
from scoped.contrib.flask import Scoped

app = Flask(__name__)
app.config["SCOPED_DATABASE_URL"] = "postgresql://localhost/mydb"
app.config["SCOPED_API_KEY"] = "sk-scoped-production-key"
app.config["SCOPED_PRINCIPAL_HEADER"] = "X-Scoped-Principal-Id"

scoped_ext = Scoped(app)
```

### Application Factory Pattern

When using the application factory pattern, create the extension without
an app and call `init_app()` later.

```python
from scoped.contrib.flask import Scoped

scoped_ext = Scoped()


def create_app():
    app = Flask(__name__)
    app.config.from_object("config.ProductionConfig")

    scoped_ext.init_app(app)

    # Register your blueprints
    from myapp.views import main_bp
    app.register_blueprint(main_bp)

    return app
```

`init_app()` performs the same initialization as the constructor: it reads
configuration, sets up the backend, and registers request hooks. You can
call `init_app()` with multiple app instances if needed (for example, in
testing).

## Configuration Keys

All configuration is read from `app.config`.

| Key | Required | Default | Description |
|---|---|---|---|
| `SCOPED_DATABASE_URL` | Yes | -- | Database connection URL for the pyscoped backend. |
| `SCOPED_API_KEY` | Yes | -- | API key for pyscoped operations. |
| `SCOPED_PRINCIPAL_HEADER` | No | `"X-Scoped-Principal-Id"` | HTTP header name used to read the principal ID from incoming requests. |

### Example Configuration Class

```python
# config.py

class BaseConfig:
    SCOPED_PRINCIPAL_HEADER = "X-Scoped-Principal-Id"


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    SCOPED_DATABASE_URL = "sqlite:///dev.db"
    SCOPED_API_KEY = "sk-scoped-dev-key"


class ProductionConfig(BaseConfig):
    DEBUG = False
    SCOPED_DATABASE_URL = "postgresql://scoped:secret@db.internal/scoped"
    SCOPED_API_KEY = "sk-scoped-production-key"


class TestingConfig(BaseConfig):
    TESTING = True
    SCOPED_DATABASE_URL = "sqlite:///:memory:"
    SCOPED_API_KEY = "sk-scoped-test-key"
```

## Per-Request Context

On each request the extension:

1. Reads the principal ID from the header specified by
   `SCOPED_PRINCIPAL_HEADER`.
2. If a principal ID is present, creates a `ScopedContext` and stores it on
   `g.scoped_context`. This context is active for the duration of the
   request.
3. On teardown, the context is cleaned up automatically.

If no principal header is present, the request proceeds without a scoped
context. Routes that require a principal should check explicitly or use a
decorator.

## Using scoped.objects in Route Handlers

Once the extension is initialized and the middleware sets up the context,
you can use `scoped.objects` in any route handler.

```python
import scoped
from flask import Flask, jsonify, request

app = Flask(__name__)
# ... extension setup ...


@app.route("/documents")
def list_documents():
    docs = scoped.objects.filter(kind="document")
    return jsonify({"documents": [d.to_dict() for d in docs]})


@app.route("/documents/<object_id>")
def get_document(object_id):
    doc = scoped.objects.get(object_id)
    if doc is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(doc.to_dict())


@app.route("/documents", methods=["POST"])
def create_document():
    data = request.get_json()
    doc = scoped.objects.create(kind="document", data=data)
    return jsonify({"id": doc.id, "status": "created"}), 201
```

## Admin Blueprint

The extension automatically registers an admin blueprint that exposes a
health check endpoint.

| Route | Method | Description |
|---|---|---|
| `/scoped/health` | GET | Returns backend health status including connection state, principal count, and object count. |

The health endpoint returns JSON:

```json
{
    "status": "healthy",
    "backend": "SQLAlchemyBackend",
    "database_url": "postgresql://.../*****",
    "principals": 142,
    "objects": 8491,
    "scopes": 37
}
```

To disable the admin blueprint, pass `admin_blueprint=False` when
initializing:

```python
scoped_ext = Scoped(app, admin_blueprint=False)
```

## Full Example Application

```python
import scoped
from flask import Flask, jsonify, request, g
from scoped.contrib.flask import Scoped


def create_app():
    app = Flask(__name__)
    app.config["SCOPED_DATABASE_URL"] = "postgresql://localhost/mydb"
    app.config["SCOPED_API_KEY"] = "sk-scoped-production-key"
    app.config["SCOPED_PRINCIPAL_HEADER"] = "X-Scoped-Principal-Id"

    scoped_ext = Scoped(app)

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.route("/me")
    def me():
        ctx = g.get("scoped_context")
        if ctx is None:
            return jsonify({"error": "no principal"}), 401
        return jsonify({
            "principal_id": ctx.principal_id,
        })

    @app.route("/objects")
    def list_objects():
        kind = request.args.get("kind")
        if kind:
            objects = scoped.objects.filter(kind=kind)
        else:
            objects = scoped.objects.all()
        return jsonify({
            "objects": [obj.to_dict() for obj in objects],
            "count": len(objects),
        })

    @app.route("/objects", methods=["POST"])
    def create_object():
        payload = request.get_json()
        obj = scoped.objects.create(
            kind=payload["kind"],
            data=payload.get("data", {}),
        )
        return jsonify({"id": obj.id}), 201

    @app.route("/objects/<object_id>", methods=["DELETE"])
    def delete_object(object_id):
        scoped.objects.delete(object_id)
        return "", 204

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
```

## Testing

Use Flask's test client along with a test configuration that points to an
in-memory database.

```python
import pytest
from myapp import create_app


@pytest.fixture
def app():
    app = create_app()
    app.config.update({
        "TESTING": True,
        "SCOPED_DATABASE_URL": "sqlite:///:memory:",
        "SCOPED_API_KEY": "sk-scoped-test-key",
    })
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_list_objects(client):
    response = client.get(
        "/objects",
        headers={"X-Scoped-Principal-Id": "test-principal"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert "objects" in data


def test_no_principal_returns_empty(client):
    response = client.get("/objects")
    assert response.status_code == 200
```

## Notes

- The extension stores its state on `app.extensions["scoped"]`, following
  Flask's standard extension pattern.
- `scoped.objects` relies on the context set by the `before_request` hook.
  Calling it outside of a request context (e.g., in a background task) will
  raise a `RuntimeError`. For background work, create a `ScopedContext`
  explicitly.
- The admin blueprint uses the `/scoped` URL prefix. If this conflicts with
  your application routes, disable it and register your own health endpoint.
- Database connections are managed by the backend and respect Flask's
  application context lifecycle. Connections are returned to the pool on
  teardown.
