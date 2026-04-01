---
title: "Client & Initialization"
description: "API reference for ScopedClient, the primary entry point for configuring and interacting with pyscoped."
category: "API Reference"
---

# Client & Initialization

The `ScopedClient` class is the root entry point for the pyscoped library. It manages
database connections, authentication, namespace access, and synchronization. You can
also use the module-level `init()` function for a simpler global-client pattern.

---

## ScopedClient

```python
from pyscoped import ScopedClient

client = ScopedClient(
    database_url="sqlite:///app.db",
    api_key="psc_live_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
    backend=None,
    sync_config=None,
)
```

### Constructor

```python
ScopedClient(
    database_url: str | None = None,
    api_key: str | None = None,
    backend: StorageBackend | None = None,
    sync_config: SyncConfig | None = None,
)
```

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `database_url` | `str \| None` | `None` | Connection URL for the storage backend. `None` uses an in-memory SQLite database. See [URL Parsing](#url-parsing) for supported formats. |
| `api_key` | `str \| None` | `None` | API key for remote sync and cloud features. Must match the format `psc_live_<32hex>` or `psc_test_<32hex>`. |
| `backend` | `StorageBackend \| None` | `None` | An explicit storage backend instance. When provided, `database_url` is ignored. Useful for custom or pre-configured backends. |
| `sync_config` | `SyncConfig \| None` | `None` | Configuration for background synchronization (interval, conflict strategy, retry policy). |

#### Raises

| Exception | Condition |
|---|---|
| `InvalidAPIKeyError` | `api_key` is provided but does not match the required format. |
| `StorageInitError` | The database cannot be reached or initialized. |

### URL Parsing

The `database_url` parameter accepts the following formats:

| Value | Resulting Backend |
|---|---|
| `None` | In-memory SQLite (`:memory:`). Data is lost when the client closes. |
| `"sqlite:///path/to/db.sqlite"` | File-backed SQLite at the given path. Created if it does not exist. |
| `"postgresql://user:pass@host:5432/dbname"` | PostgreSQL via asyncpg connection pool. |

```python
# In-memory (testing / ephemeral)
client = ScopedClient()

# SQLite file
client = ScopedClient(database_url="sqlite:///var/data/scoped.db")

# PostgreSQL
client = ScopedClient(
    database_url="postgresql://scoped:secret@db.internal:5432/scoped_prod"
)
```

---

## Namespace Properties

Each namespace is lazily initialized on first access and cached for the lifetime of
the client.

| Property | Type | Description |
|---|---|---|
| `client.principals` | `PrincipalsNamespace` | Create, retrieve, and manage principals (users, services, groups). |
| `client.objects` | `ObjectsNamespace` | Versioned, scoped object storage with ownership and soft-delete. |
| `client.scopes` | `ScopesNamespace` | Scope lifecycle, membership, projection, and hierarchy. |
| `client.audit` | `AuditNamespace` | Append-only, hash-chained audit trail queries and verification. |
| `client.secrets` | `SecretsNamespace` | Encrypted secret storage, rotation, and reference-based sharing. |

```python
client = ScopedClient(database_url="sqlite:///app.db")

# Each property returns the same cached namespace instance
assert client.principals is client.principals
```

---

## as_principal

```python
client.as_principal(principal: str | Principal) -> ContextManager[ScopedClient]
```

Returns a context manager that sets the acting principal for all operations within
its block. Nested calls are supported; the innermost principal wins.

### Parameters

| Parameter | Type | Description |
|---|---|---|
| `principal` | `str \| Principal` | A principal ID string or a `Principal` model instance. |

### Returns

A context manager that yields the same `ScopedClient` with the principal bound.

### Example

```python
from pyscoped import ScopedClient

client = ScopedClient(database_url="sqlite:///app.db")

admin = client.principals.create(display_name="Admin", kind="user")

with client.as_principal(admin):
    # All operations inside this block are attributed to `admin`
    obj, version = client.objects.create(
        object_type="document",
        data={"title": "Design Spec"},
    )

    # Nested principal override
    service = client.principals.create(display_name="CI Bot", kind="service")
    with client.as_principal(service):
        client.objects.update(obj.id, data={"status": "reviewed"})
```

---

## Sync Methods

These methods control background synchronization between the local backend and a
remote pyscoped cloud instance. They require a valid `api_key` and `sync_config`.

### start_sync

```python
client.start_sync() -> None
```

Starts the background sync worker. Raises `SyncNotConfiguredError` if no
`sync_config` was provided at construction time.

### pause_sync

```python
client.pause_sync() -> None
```

Pauses the sync worker without tearing down its internal state. Buffered changes
are retained and will be pushed on resume.

### resume_sync

```python
client.resume_sync() -> None
```

Resumes a previously paused sync worker.

### stop_sync

```python
client.stop_sync() -> None
```

Stops the sync worker and releases its resources. Buffered but unsent changes are
persisted locally and will be sent when `start_sync` is called again.

### sync_status

```python
client.sync_status() -> SyncStatus
```

Returns a `SyncStatus` dataclass describing the current sync state.

| Field | Type | Description |
|---|---|---|
| `state` | `str` | One of `"idle"`, `"running"`, `"paused"`, `"stopped"`, `"error"`. |
| `last_synced_at` | `datetime \| None` | Timestamp of the last successful sync cycle. |
| `pending_changes` | `int` | Number of local changes waiting to be pushed. |
| `error` | `str \| None` | Description of the last sync error, if any. |

### verify_sync

```python
client.verify_sync() -> bool
```

Performs an on-demand consistency check between the local store and the remote.
Returns `True` if both sides are identical, `False` otherwise.

### Example

```python
from pyscoped import ScopedClient, SyncConfig

client = ScopedClient(
    database_url="sqlite:///local.db",
    api_key="psc_live_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
    sync_config=SyncConfig(interval_seconds=30, conflict_strategy="server_wins"),
)

client.start_sync()
status = client.sync_status()
print(status.state)        # "running"
print(status.pending_changes)  # 0

client.pause_sync()
# ... offline work ...
client.resume_sync()

assert client.verify_sync()
client.stop_sync()
```

---

## close and Context Manager Protocol

### close

```python
client.close() -> None
```

Shuts down the client, stops any running sync workers, and closes all database
connections. The client must not be used after calling `close()`.

### Context Manager

`ScopedClient` implements `__enter__` and `__exit__`, allowing use as a context
manager that automatically calls `close()` on exit.

```python
with ScopedClient(database_url="sqlite:///app.db") as client:
    client.principals.create(display_name="Alice", kind="user")
# client.close() is called automatically here
```

---

## Services Escape Hatch

```python
client.services -> ServiceRegistry
```

The `services` property exposes the internal service registry for advanced or
low-level operations that are not surfaced through the namespace APIs. This is
intended for library authors and plugin developers.

```python
relationship = client.services.principal_service.create_relationship(
    parent_id=org.id,
    child_id=team_member.id,
    relationship_type="member_of",
)
```

> **Warning:** The services API is not covered by the same stability guarantees as the
> namespace APIs. Method signatures may change between minor versions.

---

## Module-Level init Function

```python
pyscoped.init(
    database_url: str | None = None,
    api_key: str | None = None,
    backend: StorageBackend | None = None,
    sync_config: SyncConfig | None = None,
) -> ScopedClient
```

Creates a `ScopedClient` and installs it as the global default. Subsequent access
to `pyscoped.principals`, `pyscoped.objects`, and the other module-level namespace
shortcuts will delegate to this client. The function is **thread-safe**; concurrent
calls are serialized, and the first caller wins unless `force=True` is passed.

### Parameters

Same as the `ScopedClient` constructor, plus:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `force` | `bool` | `False` | Replace an existing global client if one is already initialized. |

### Returns

The newly created `ScopedClient` instance.

### Raises

| Exception | Condition |
|---|---|
| `AlreadyInitializedError` | A global client already exists and `force` is `False`. |

### Example

```python
import pyscoped

pyscoped.init(database_url="sqlite:///app.db")

# Module-level access delegates to the global client
user = pyscoped.principals.create(display_name="Alice", kind="user")

with pyscoped.as_principal(user):
    doc, v = pyscoped.objects.create(
        object_type="note",
        data={"body": "Hello, world!"},
    )
```

---

## Module-Level Namespace Access

After calling `pyscoped.init()`, the following module-level attributes are available
as convenient shortcuts:

| Attribute | Equivalent |
|---|---|
| `pyscoped.principals` | `_global_client.principals` |
| `pyscoped.objects` | `_global_client.objects` |
| `pyscoped.scopes` | `_global_client.scopes` |
| `pyscoped.audit` | `_global_client.audit` |
| `pyscoped.secrets` | `_global_client.secrets` |
| `pyscoped.as_principal(p)` | `_global_client.as_principal(p)` |

Accessing any of these before calling `init()` raises `ClientNotInitializedError`.

```python
import pyscoped

# Raises ClientNotInitializedError
# pyscoped.principals.list()

pyscoped.init()
pyscoped.principals.list()  # works
```

---

## Full Lifecycle Example

```python
import pyscoped
from pyscoped import ScopedClient, SyncConfig

# Option A: explicit client
client = ScopedClient(
    database_url="postgresql://scoped:s3cret@localhost:5432/myapp",
    api_key="psc_live_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
    sync_config=SyncConfig(interval_seconds=60),
)

with client:
    client.start_sync()

    admin = client.principals.create(display_name="Admin", kind="user")
    with client.as_principal(admin):
        scope = client.scopes.create(name="engineering")
        doc, v1 = client.objects.create(
            object_type="spec",
            data={"title": "RFC-42"},
        )
        client.scopes.add_member(scope.id, admin.id, role="owner")

    print(client.sync_status())
# close() called automatically

# Option B: global init
pyscoped.init(database_url="sqlite:///local.db")
pyscoped.principals.create(display_name="Bob", kind="user")
```
