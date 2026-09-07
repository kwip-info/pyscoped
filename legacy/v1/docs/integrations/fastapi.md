---
title: FastAPI Integration
description: Integrate pyscoped with FastAPI for automatic scoped context in HTTP and WebSocket handlers, with async-native dependencies and built-in management routes.
category: integrations
---

# FastAPI Integration

pyscoped provides an async-native FastAPI integration with middleware for HTTP
and WebSocket requests, dependency injection helpers, and a management router.

## Installation

```bash
pip install pyscoped[fastapi]
```

This installs pyscoped along with FastAPI-specific dependencies (Starlette,
asyncpg, and related packages).

## Quick Start

```python
from fastapi import FastAPI
from scoped.contrib.fastapi import ScopedContextMiddleware

app = FastAPI()

app.add_middleware(
    ScopedContextMiddleware,
    database_url="postgresql+asyncpg://localhost/mydb",
    api_key="sk-scoped-...",
)
```

## Middleware Configuration

`ScopedContextMiddleware` accepts the following parameters:

| Parameter | Type | Description |
|---|---|---|
| `app` | `ASGIApp` | The ASGI application (passed automatically by FastAPI). |
| `database_url` | `str` | Database connection URL for the backend. |
| `api_key` | `str` | pyscoped API key. |
| `principal_header` | `str` | HTTP header name for principal ID. Defaults to `"X-Scoped-Principal-Id"`. |
| `principal_resolver` | `callable` | Optional callable that receives the request/connection and returns a principal ID. Can be sync or async. |
| `exempt_paths` | `list[str]` | URL path prefixes to skip context setup for (e.g., `["/docs", "/health"]`). |

### Full Middleware Example

```python
from fastapi import FastAPI
from scoped.contrib.fastapi import ScopedContextMiddleware


async def resolve_principal(request):
    """Extract principal from a JWT bearer token."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        claims = await verify_jwt(token)
        return claims.get("sub")
    return None


app = FastAPI()

app.add_middleware(
    ScopedContextMiddleware,
    database_url="postgresql+asyncpg://localhost/mydb",
    api_key="sk-scoped-production-key",
    principal_header="X-Scoped-Principal-Id",
    principal_resolver=resolve_principal,
    exempt_paths=["/docs", "/openapi.json", "/health"],
)
```

## HTTP Request Handling

For standard HTTP requests the middleware runs before each request and:

1. Checks whether the path is exempt. If so, the request proceeds without a
   scoped context.
2. Calls `principal_resolver` if configured. The resolver can be synchronous
   or asynchronous -- the middleware uses `inspect.isawaitable` to detect
   coroutines and awaits them automatically.
3. Falls back to reading the `principal_header` from the request headers.
4. If a principal ID is resolved, a `ScopedContext` is created and attached
   to the request state for the duration of the request.

## WebSocket Support

The middleware handles WebSocket connections by extracting the principal
from the handshake headers. The `ScopedContext` is set once during the
handshake and persists for the entire lifetime of the WebSocket connection.

```python
from fastapi import FastAPI, WebSocket
import scoped

app = FastAPI()

# ... middleware setup ...

@app.websocket("/ws/updates")
async def updates_ws(websocket: WebSocket):
    await websocket.accept()

    # The ScopedContext was set during the handshake.
    # All scoped.objects calls use the connected principal's context.
    objects = scoped.objects.filter(kind="notification")
    for obj in objects:
        await websocket.send_json(obj.to_dict())

    try:
        while True:
            data = await websocket.receive_json()
            # Process incoming messages within the same scoped context.
            result = await scoped.objects.create(
                kind="message",
                data=data,
            )
            await websocket.send_json({"status": "created", "id": result.id})
    except Exception:
        await websocket.close()
```

Because the context persists across the full connection lifetime, you do not
need to re-resolve the principal for each frame.

## Dependencies

The `scoped.contrib.fastapi.dependencies` module provides injectable
dependencies for use with FastAPI's `Depends()`.

### get_client

Returns the pyscoped client singleton.

```python
from fastapi import Depends
from scoped.contrib.fastapi.dependencies import get_client
from scoped import ScopedClient


@app.get("/admin/principals")
async def list_principals(client: ScopedClient = Depends(get_client)):
    principals = await client.list_principals()
    return {"principals": principals}
```

### get_context

Returns the current `ScopedContext` for the request.

```python
from scoped.contrib.fastapi.dependencies import get_context
from scoped.identity.context import ScopedContext


@app.get("/debug/context")
async def debug_context(ctx: ScopedContext = Depends(get_context)):
    return {
        "principal_id": ctx.principal_id,
        "scope_id": ctx.scope_id,
    }
```

### get_principal

Returns the resolved principal object for the current request.

```python
from scoped.contrib.fastapi.dependencies import get_principal
from scoped.identity.principal import Principal


@app.get("/me")
async def current_user(principal: Principal = Depends(get_principal)):
    return {
        "id": principal.id,
        "name": principal.name,
        "created_at": principal.created_at.isoformat(),
    }
```

## Management Router

The `scoped.contrib.fastapi.router` module provides a pre-built APIRouter
with health and audit endpoints.

```python
from scoped.contrib.fastapi.router import router as scoped_router

app.include_router(scoped_router)
```

This registers the following routes:

| Route | Method | Description |
|---|---|---|
| `/scoped/health` | GET | Returns backend health status and diagnostics. |
| `/scoped/audit` | GET | Returns recent audit log entries. Accepts `limit`, `principal`, and `action` query parameters. |

### Customizing the Router Prefix

```python
app.include_router(scoped_router, prefix="/admin")
# Routes become /admin/scoped/health and /admin/scoped/audit
```

## Async Principal Resolver

The `principal_resolver` parameter accepts both synchronous and asynchronous
callables. The middleware detects which type was provided and handles it
appropriately.

```python
# Synchronous resolver
def sync_resolver(request):
    api_key = request.headers.get("x-api-key")
    return lookup_principal_by_api_key(api_key)


# Asynchronous resolver
async def async_resolver(request):
    api_key = request.headers.get("x-api-key")
    return await async_lookup_principal_by_api_key(api_key)
```

Internally, the middleware calls the resolver and then checks the return
value with `inspect.isawaitable()`. If the result is awaitable, it is
awaited before proceeding.

## Full Example Application

```python
from fastapi import FastAPI, Depends, WebSocket
from scoped.contrib.fastapi import ScopedContextMiddleware
from scoped.contrib.fastapi.dependencies import get_client, get_principal
from scoped.contrib.fastapi.router import router as scoped_router
import scoped


async def resolve_principal(request):
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        claims = await verify_jwt(auth[7:])
        return claims["sub"]
    return None


app = FastAPI(title="My Application")

app.add_middleware(
    ScopedContextMiddleware,
    database_url="postgresql+asyncpg://localhost/mydb",
    api_key="sk-scoped-production-key",
    principal_resolver=resolve_principal,
    exempt_paths=["/docs", "/openapi.json"],
)

app.include_router(scoped_router)


@app.get("/me")
async def me(principal=Depends(get_principal)):
    return {"id": principal.id, "name": principal.name}


@app.get("/documents")
async def list_documents():
    docs = scoped.objects.filter(kind="document")
    return {"documents": [d.to_dict() for d in docs]}


@app.post("/documents")
async def create_document(payload: dict):
    doc = await scoped.objects.create(kind="document", data=payload)
    return {"id": doc.id, "status": "created"}


@app.websocket("/ws/live")
async def live_feed(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            msg = await websocket.receive_json()
            result = scoped.objects.filter(kind=msg.get("kind", "event"))
            await websocket.send_json([r.to_dict() for r in result])
    except Exception:
        await websocket.close()
```

## Notes

- The middleware stores the `ScopedContext` on `request.state.scoped_context`.
  The `get_context` dependency reads from this attribute.
- For testing, you can override the dependencies using FastAPI's standard
  `app.dependency_overrides` mechanism to inject mock clients or contexts.
- The async backend uses connection pooling. Configure pool size via the
  database URL parameters (e.g., `?pool_size=10&max_overflow=20`).
- When deploying behind a reverse proxy, verify that the `principal_header`
  is forwarded correctly and is not stripped by the proxy.
