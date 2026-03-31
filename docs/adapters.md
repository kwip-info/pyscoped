# Framework Adapters Guide

Scoped ships four optional framework adapters under `scoped.contrib`. Each adapter integrates Scoped's identity, audit, and tenancy machinery into your web framework or protocol server with minimal configuration.

All adapters share the same underlying pattern:

1. Resolve the acting principal from the incoming request.
2. Wrap the request handler in a `ScopedContext` so every operation is attributed to that principal.
3. Provide admin endpoints or commands for health checks and audit trail queries.

Shared utilities live in `scoped.contrib._base`:

- **`resolve_principal_from_id(backend, principal_id)`** -- looks up a principal by ID.
- **`build_services(backend)`** -- creates the standard set of Scoped services (principals, manager, scopes, projections, audit, rules, health) from a single backend.

---

## Django (`scoped.contrib.django`)

### Installation

```bash
pip install pyscoped[django]
```

### Configuration

Add Scoped to your Django settings:

```python
# settings.py

INSTALLED_APPS = [
    # ...
    "scoped.contrib.django",
]

MIDDLEWARE = [
    # ...
    "scoped.contrib.django.middleware.ScopedContextMiddleware",
]

# Optional settings (shown with defaults):
SCOPED_BACKEND_USING = "default"              # Django DB alias
SCOPED_PRINCIPAL_HEADER = "HTTP_X_SCOPED_PRINCIPAL_ID"  # request.META key
SCOPED_PRINCIPAL_RESOLVER = None              # dotted path to callable(request) -> Principal
SCOPED_EXEMPT_PATHS = []                      # path prefixes to skip
```

### How It Works

When the `ScopedConfig` app is ready, it automatically creates a `DjangoORMBackend` and calls `initialize()` to ensure all Scoped tables exist.

The `ScopedContextMiddleware` runs on every request (except exempt paths):

1. Resolves the principal -- first via `SCOPED_PRINCIPAL_RESOLVER` if set, otherwise by reading the `SCOPED_PRINCIPAL_HEADER` from `request.META`.
2. Opens a `ScopedContext` for the resolved principal.
3. Attaches the context to `request.scoped_context`.
4. Closes the context after the response completes.

If no principal can be resolved, the request proceeds without a Scoped context.

### DjangoORMBackend

The `DjangoORMBackend` implements `StorageBackend` using Django's database connection. It translates SQLite-style `?` placeholders to Django-style `%s` and adapts DDL for PostgreSQL when needed.

```python
from scoped.contrib.django.backend import DjangoORMBackend

backend = DjangoORMBackend(using="default", auto_create_tables=True)
```

Key features:

- Uses Django's connection pooling and transaction machinery.
- Supports savepoints for nested transactions.
- Automatically adapts schema DDL for the target database vendor (SQLite, PostgreSQL).

### Custom Principal Resolution

For production use, you will typically resolve principals from your Django user model rather than a raw header:

```python
# myapp/scoped_auth.py
from scoped.identity.principal import PrincipalStore
from scoped.contrib.django import get_backend

def resolve_principal(request):
    if not request.user.is_authenticated:
        return None
    store = PrincipalStore(get_backend())
    return store.find_principal(str(request.user.pk))
```

```python
# settings.py
SCOPED_PRINCIPAL_RESOLVER = "myapp.scoped_auth.resolve_principal"
```

### Management Commands

Three management commands are available once `scoped.contrib.django` is in `INSTALLED_APPS`:

#### `scoped_health`

Runs all Scoped framework health checks and reports pass/fail status.

```bash
python manage.py scoped_health
```

```
  [PASS] storage: Backend reachable
  [PASS] schema: All tables present
  [PASS] registry: URN resolution working

All health checks passed.
```

#### `scoped_audit`

Queries the Scoped audit trail with optional filters.

```bash
python manage.py scoped_audit --actor abc123 --limit 10
python manage.py scoped_audit --target def456 --action create
```

Options:

| Flag | Description |
|------|-------------|
| `--actor` | Filter by actor ID |
| `--target` | Filter by target ID |
| `--action` | Filter by action type |
| `--limit` | Max entries (default 20) |

#### `scoped_compliance`

Runs a full compliance report covering health checks and registry introspection.

```bash
python manage.py scoped_compliance
python manage.py scoped_compliance --no-health --no-introspection
```

Options:

| Flag | Description |
|------|-------------|
| `--no-health` | Skip health checks |
| `--no-introspection` | Skip registry introspection |

### Accessing Scoped Services in Views

```python
from scoped.contrib.django import get_backend
from scoped.contrib._base import build_services

def my_view(request):
    services = build_services(get_backend())
    manager = services["manager"]
    # Use request.scoped_context for the acting principal
    ctx = request.scoped_context
    obj = manager.get(object_id, principal_id=ctx.principal.id)
    # ...
```

---

## FastAPI (`scoped.contrib.fastapi`)

### Installation

```bash
pip install pyscoped[fastapi]
```

### Basic Setup

```python
from fastapi import FastAPI
from scoped.storage.sqlite import SQLiteBackend
from scoped.contrib.fastapi.middleware import ScopedContextMiddleware
from scoped.contrib.fastapi.router import router as scoped_router

# Initialize backend
backend = SQLiteBackend("app.db")
backend.initialize()

# Create app
app = FastAPI()
app.add_middleware(ScopedContextMiddleware, backend=backend)
app.include_router(scoped_router)  # adds /scoped/health, /scoped/audit
```

### ScopedContextMiddleware

The middleware wraps each request in a `ScopedContext`:

```python
app.add_middleware(
    ScopedContextMiddleware,
    backend=backend,
    principal_header="x-scoped-principal-id",  # default
    principal_resolver=None,                    # optional async/sync callable
    exempt_paths=["/docs", "/openapi.json"],    # paths to skip
)
```

Parameters:

| Parameter | Type | Description |
|-----------|------|-------------|
| `backend` | `StorageBackend` | Backend instance (also sets the global backend for dependency injection) |
| `principal_header` | `str` | HTTP header name for principal ID (default `x-scoped-principal-id`) |
| `principal_resolver` | `callable` | Optional `(Request) -> Principal` (supports both sync and async) |
| `exempt_paths` | `list[str]` | Path prefixes to skip |

The middleware stores the context on `request.state.scoped_context`.

### Dependencies

FastAPI dependency injection functions for use with `Depends()`:

```python
from fastapi import Depends
from scoped.contrib.fastapi.dependencies import (
    get_scoped_context,
    get_principal,
    get_backend,
    get_services,
)

@app.get("/my-objects")
def list_objects(
    principal=Depends(get_principal),
    services=Depends(get_services),
):
    manager = services["manager"]
    obj = manager.get(some_id, principal_id=principal.id)
    return {"object": obj}
```

| Dependency | Returns | Raises |
|------------|---------|--------|
| `get_scoped_context` | The active `ScopedContext` | 401 if no context |
| `get_principal` | The acting `Principal` | 401 if no context |
| `get_backend` | The global `StorageBackend` | `RuntimeError` if not configured |
| `get_services` | Full service dict from `build_services()` | `RuntimeError` if no backend |

### Built-in Router

The pre-built router provides two admin endpoints:

```python
from scoped.contrib.fastapi.router import router as scoped_router

app.include_router(scoped_router)
```

#### `GET /scoped/health`

Returns framework health status.

```json
{
  "healthy": true,
  "checks": {
    "storage": {"name": "storage", "passed": true, "detail": "Backend reachable"},
    "schema": {"name": "schema", "passed": true, "detail": "All tables present"}
  }
}
```

#### `GET /scoped/audit`

Queries the audit trail. Accepts query parameters:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `actor_id` | `str` | -- | Filter by actor |
| `target_id` | `str` | -- | Filter by target |
| `limit` | `int` | 50 | Max entries |

```json
[
  {
    "id": "abc123",
    "sequence": 1,
    "actor_id": "user-1",
    "action": "create",
    "target_type": "document",
    "target_id": "doc-1",
    "timestamp": "2026-03-30T12:00:00",
    "hash": "a1b2c3..."
  }
]
```

### Pydantic Schemas

Response schemas are available for building custom endpoints:

```python
from scoped.contrib.fastapi.schemas import (
    PrincipalSchema,      # id, kind, display_name, created_at, lifecycle
    ScopedObjectSchema,   # id, object_type, owner_id, current_version, created_at, lifecycle
    ScopeSchema,          # id, name, owner_id, lifecycle, created_at
    TraceEntrySchema,     # id, sequence, actor_id, action, target_type, target_id, timestamp, hash
    HealthCheckSchema,    # name, passed, detail
    HealthStatusSchema,   # healthy, checks
)

# Convert from Scoped dataclasses:
schema = PrincipalSchema.from_principal(principal)
schema = ScopedObjectSchema.from_object(obj)
schema = ScopeSchema.from_scope(scope)
schema = TraceEntrySchema.from_entry(audit_entry)
```

---

## Flask (`scoped.contrib.flask`)

### Installation

```bash
pip install pyscoped[flask]
```

### Basic Setup

```python
from flask import Flask
from scoped.contrib.flask.extension import ScopedExtension
from scoped.contrib.flask.admin import admin_bp

app = Flask(__name__)
scoped = ScopedExtension(app)
app.register_blueprint(admin_bp)  # adds /scoped/health, /scoped/audit
```

### ScopedExtension

The extension supports both direct initialization and the `init_app` pattern:

```python
# Direct
scoped = ScopedExtension(app)

# Deferred (application factory pattern)
scoped = ScopedExtension()
scoped.init_app(app)
```

On `init_app`, the extension:

1. Creates a `SQLiteBackend` from configuration and calls `initialize()`.
2. Builds the full service set via `build_services()`.
3. Registers `before_request` and `teardown_request` hooks for ScopedContext lifecycle.

### Configuration

Flask `app.config` keys:

| Key | Default | Description |
|-----|---------|-------------|
| `SCOPED_STORAGE_BACKEND` | `"sqlite"` | Storage backend type |
| `SCOPED_SQLITE_PATH` | `":memory:"` | SQLite database path |
| `SCOPED_PRINCIPAL_HEADER` | `"X-Scoped-Principal-Id"` | HTTP header for principal ID |
| `SCOPED_PRINCIPAL_RESOLVER` | `None` | Callable `(request) -> Principal` |
| `SCOPED_EXEMPT_PATHS` | `[]` | Path prefixes to skip |

### Request Context

During each request, the extension:

1. Resolves the principal from the configured header or custom resolver.
2. Opens a `ScopedContext` and stores it on `g.scoped_context`.
3. Closes the context on teardown.

Access the context in your routes:

```python
from flask import g

@app.route("/my-resource")
def my_resource():
    ctx = g.scoped_context
    if ctx is None:
        return {"error": "Not authenticated"}, 401
    # ctx.principal is the acting principal
    return {"principal": ctx.principal.id}
```

### Accessing Services

The extension provides access to the backend and the full service set:

```python
from flask import current_app

@app.route("/objects/<object_id>")
def get_object(object_id):
    ext = current_app.extensions["scoped"]
    manager = ext.services["manager"]
    obj = manager.get(object_id, principal_id=g.scoped_context.principal.id)
    # ...
```

### Admin Blueprint

The admin blueprint provides two JSON endpoints:

```python
from scoped.contrib.flask.admin import admin_bp

app.register_blueprint(admin_bp)  # mounted at /scoped
```

#### `GET /scoped/health`

Returns framework health status:

```json
{
  "healthy": true,
  "checks": {
    "storage": {"passed": true, "detail": "Backend reachable"}
  }
}
```

#### `GET /scoped/audit`

Queries the audit trail. Accepts query parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `actor_id` | -- | Filter by actor |
| `target_id` | -- | Filter by target |
| `limit` | 50 | Max entries |

---

## MCP (`scoped.contrib.mcp`)

### Installation

```bash
pip install pyscoped[mcp]
```

This installs the [MCP SDK](https://github.com/modelcontextprotocol/python-sdk) (`mcp`) as a dependency.

### Basic Setup

```python
from scoped.storage.sqlite import SQLiteBackend
from scoped.contrib.mcp.server import create_scoped_server

backend = SQLiteBackend("app.db")
backend.initialize()

mcp = create_scoped_server(backend)
mcp.run()
```

`create_scoped_server()` accepts:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `backend` | `StorageBackend` | (required) | An initialized storage backend |
| `name` | `str` | `"scoped"` | Server name |

The function creates a `FastMCP` instance, registers all Scoped tools and resources, and returns the server ready to run.

### Available Tools

Six tools are registered for AI agent interaction:

#### `create_principal`

Create a new Scoped principal.

| Parameter | Type | Description |
|-----------|------|-------------|
| `kind` | `str` | Principal type (e.g., `"user"`, `"bot"`, `"team"`) |
| `display_name` | `str` | Human-readable name |

Returns: `{"id", "kind", "display_name"}`

#### `create_object`

Create a new scoped object (creator-private by default).

| Parameter | Type | Description |
|-----------|------|-------------|
| `object_type` | `str` | Object type (e.g., `"document"`, `"task"`) |
| `owner_id` | `str` | Principal ID of the owner |
| `data` | `dict` | Object data payload |

Returns: `{"object_id", "version", "object_type", "owner_id"}`

#### `get_object`

Get an object by ID (owner-only access).

| Parameter | Type | Description |
|-----------|------|-------------|
| `object_id` | `str` | Object ID to retrieve |
| `principal_id` | `str` | Requesting principal's ID |

Returns: `{"id", "object_type", "owner_id", "current_version", "lifecycle"}` or `"Object not found or access denied"`.

#### `create_scope`

Create a new scope (sharing boundary).

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Scope name |
| `owner_id` | `str` | Principal ID of the scope owner |
| `description` | `str` | Optional description (default `""`) |

Returns: `{"scope_id", "name", "owner_id"}`

#### `list_audit`

Query the audit trail.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `actor_id` | `str` | `""` | Filter by actor (empty = all) |
| `target_id` | `str` | `""` | Filter by target (empty = all) |
| `limit` | `int` | `20` | Max entries |

Returns: list of `{"id", "action", "actor_id", "target_type", "target_id", "timestamp"}`

#### `health_check`

Run Scoped framework health checks. Takes no parameters.

Returns: `{"healthy", "checks"}` where each check has `{"passed", "detail"}`.

### Available Resources

Three MCP resources are registered for data access:

| URI | Description |
|-----|-------------|
| `scoped://principals` | JSON list of all principals with id, kind, display_name, lifecycle |
| `scoped://health` | Current framework health status |
| `scoped://audit/recent` | Most recent 50 audit entries |

### Example: Claude Desktop Configuration

To use Scoped as an MCP server with Claude Desktop, add it to your configuration:

```json
{
  "mcpServers": {
    "scoped": {
      "command": "python",
      "args": ["-m", "myapp.mcp_server"]
    }
  }
}
```

Where `myapp/mcp_server.py` is:

```python
from scoped.storage.sqlite import SQLiteBackend
from scoped.contrib.mcp.server import create_scoped_server

backend = SQLiteBackend("scoped.db")
backend.initialize()

server = create_scoped_server(backend)
server.run()
```
