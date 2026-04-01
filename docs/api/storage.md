---
title: "Storage Backends"
description: "API reference for storage backends, transactions, multi-tenancy, and the migration system in pyscoped."
category: "API Reference"
---

# Storage Backends

pyscoped uses a pluggable storage layer. The library ships with SQLite and PostgreSQL
backends, a multi-tenant router, and a migration system. Custom backends can be
created by implementing the `StorageBackend` abstract class.

---

## StorageBackend (Abstract)

```python
from pyscoped.storage import StorageBackend
```

The base class that all storage backends must implement.

```python
class StorageBackend(ABC):
    @property
    @abstractmethod
    def dialect(self) -> str: ...

    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    def transaction(self) -> StorageTransaction: ...

    @abstractmethod
    async def execute(self, query: str, params: tuple = ()) -> None: ...

    @abstractmethod
    async def fetch_one(self, query: str, params: tuple = ()) -> dict | None: ...

    @abstractmethod
    async def fetch_all(self, query: str, params: tuple = ()) -> list[dict]: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def table_exists(self, table_name: str) -> bool: ...
```

### Methods

#### dialect

```python
@property
def dialect(self) -> str
```

Returns the SQL dialect identifier. Used by the migration system and query builder
to emit dialect-appropriate SQL.

| Backend | Dialect |
|---|---|
| `SQLiteBackend` | `"sqlite"` |
| `PostgresBackend` | `"postgresql"` |

#### initialize

```python
async def initialize(self) -> None
```

Performs one-time setup: creates tables, applies pending migrations, and configures
backend-specific settings (e.g. SQLite pragmas, PostgreSQL RLS policies). Called
automatically by `ScopedClient` during construction.

#### transaction

```python
def transaction(self) -> StorageTransaction
```

Returns a new `StorageTransaction` context manager. All operations within the
transaction are atomic -- they either all commit or all roll back.

```python
async with backend.transaction() as txn:
    await txn.execute("INSERT INTO ...", (value,))
    row = await txn.fetch_one("SELECT ...", (id,))
    # auto-commits on successful exit
# auto-rolls back on exception
```

#### execute

```python
async def execute(self, query: str, params: tuple = ()) -> None
```

Executes a write query (INSERT, UPDATE, DELETE) outside of an explicit transaction.
The operation is auto-committed.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | *required* | SQL query string with `?` (SQLite) or `$1` (PostgreSQL) placeholders. |
| `params` | `tuple` | `()` | Bind parameters. |

#### fetch_one

```python
async def fetch_one(self, query: str, params: tuple = ()) -> dict | None
```

Executes a read query and returns the first row as a dictionary, or `None` if no
rows match.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | *required* | SQL query string. |
| `params` | `tuple` | `()` | Bind parameters. |

#### fetch_all

```python
async def fetch_all(self, query: str, params: tuple = ()) -> list[dict]
```

Executes a read query and returns all matching rows as a list of dictionaries.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | *required* | SQL query string. |
| `params` | `tuple` | `()` | Bind parameters. |

#### close

```python
async def close(self) -> None
```

Releases all resources held by the backend (file handles, connection pools). The
backend must not be used after calling `close()`.

#### table_exists

```python
async def table_exists(self, table_name: str) -> bool
```

Returns `True` if the specified table exists in the database.

| Parameter | Type | Description |
|---|---|---|
| `table_name` | `str` | The table name to check. |

---

## StorageTransaction

```python
from pyscoped.storage import StorageTransaction
```

A transaction context manager providing atomic, isolated database operations.

```python
class StorageTransaction:
    async def execute(self, query: str, params: tuple = ()) -> None: ...
    async def fetch_one(self, query: str, params: tuple = ()) -> dict | None: ...
    async def fetch_all(self, query: str, params: tuple = ()) -> list[dict]: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    async def __aenter__(self) -> StorageTransaction: ...
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None: ...
```

### Methods

| Method | Description |
|---|---|
| `execute(query, params)` | Execute a write query within the transaction. |
| `fetch_one(query, params)` | Fetch a single row within the transaction. |
| `fetch_all(query, params)` | Fetch all matching rows within the transaction. |
| `commit()` | Explicitly commit the transaction. Usually handled automatically by the context manager. |
| `rollback()` | Explicitly roll back the transaction. Called automatically on exception. |

### Context Manager Protocol

When used as an async context manager, the transaction auto-commits on successful
exit and auto-rolls back if an exception propagates.

```python
async with backend.transaction() as txn:
    await txn.execute(
        "INSERT INTO principals (id, display_name) VALUES (?, ?)",
        ("p-001", "Alice"),
    )
    result = await txn.fetch_one(
        "SELECT * FROM principals WHERE id = ?", ("p-001",)
    )
    # commits here
```

---

## SQLiteBackend

```python
from pyscoped.storage.sqlite import SQLiteBackend
```

File-backed or in-memory SQLite storage. Suitable for development, testing, single-
process applications, and embedded use cases.

### Constructor

```python
SQLiteBackend(path: str = ":memory:")
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `path` | `str` | `":memory:"` | File path for the SQLite database. Use `":memory:"` for an in-memory database. |

### Pragmas

On initialization, the following SQLite pragmas are set for performance and
reliability:

| Pragma | Value | Purpose |
|---|---|---|
| `journal_mode` | `WAL` | Write-ahead logging for concurrent reads during writes. |
| `synchronous` | `NORMAL` | Balance between durability and write performance. |
| `foreign_keys` | `ON` | Enforce foreign key constraints. |
| `busy_timeout` | `5000` | Wait up to 5 seconds for locks before returning SQLITE_BUSY. |
| `cache_size` | `-64000` | 64 MB page cache. |

### Example

```python
# In-memory (default)
backend = SQLiteBackend()
await backend.initialize()

# File-backed
backend = SQLiteBackend(path="/var/data/scoped.db")
await backend.initialize()

# Use with ScopedClient
client = ScopedClient(database_url="sqlite:///app.db")
# SQLiteBackend is created and initialized automatically

# Or provide explicitly
client = ScopedClient(backend=SQLiteBackend("/tmp/test.db"))
```

---

## PostgresBackend

```python
from pyscoped.storage.postgres import PostgresBackend
```

Production-grade PostgreSQL storage with connection pooling and optional row-level
security (RLS).

### Constructor

```python
PostgresBackend(
    dsn: str,
    pool_min_size: int = 2,
    pool_max_size: int = 10,
    pool_timeout: float = 30.0,
    enable_rls: bool = False,
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `dsn` | `str` | *required* | PostgreSQL connection string (e.g. `"postgresql://user:pass@host:5432/db"`). |
| `pool_min_size` | `int` | `2` | Minimum number of connections in the pool. |
| `pool_max_size` | `int` | `10` | Maximum number of connections in the pool. |
| `pool_timeout` | `float` | `30.0` | Maximum seconds to wait for a connection from the pool before raising `PoolTimeoutError`. |
| `enable_rls` | `bool` | `False` | Enable PostgreSQL row-level security policies. When `True`, the backend creates RLS policies that enforce principal-based isolation at the database level. |

### Connection Pooling

The `PostgresBackend` maintains a connection pool backed by `asyncpg`. Connections
are acquired from the pool for each operation and returned automatically.

```python
backend = PostgresBackend(
    dsn="postgresql://scoped:secret@db:5432/scoped_prod",
    pool_min_size=5,
    pool_max_size=20,
    pool_timeout=10.0,
)
await backend.initialize()

# Pool stats (via backend internals)
print(backend._pool.get_size())      # current pool size
print(backend._pool.get_idle_size()) # idle connections
```

### Row-Level Security (RLS)

When `enable_rls=True`, the backend creates PostgreSQL RLS policies on all pyscoped
tables. Each connection sets a session variable (`scoped.current_principal`) that the
RLS policies reference, ensuring that queries only return rows the current principal
is authorized to see.

```python
backend = PostgresBackend(
    dsn="postgresql://scoped:secret@db:5432/scoped_prod",
    enable_rls=True,
)
await backend.initialize()
# RLS policies are now active

# ScopedClient handles setting the session variable automatically
client = ScopedClient(backend=backend)
with client.as_principal(alice):
    # SQL queries include: SET scoped.current_principal = 'alice-id'
    docs = client.objects.list()  # only Alice's visible objects
```

### Example

```python
from pyscoped import ScopedClient
from pyscoped.storage.postgres import PostgresBackend

# Via URL (backend created automatically)
client = ScopedClient(
    database_url="postgresql://scoped:secret@localhost:5432/myapp",
)

# Explicit backend with custom pool settings
backend = PostgresBackend(
    dsn="postgresql://scoped:secret@db.internal:5432/prod",
    pool_min_size=5,
    pool_max_size=50,
    pool_timeout=15.0,
    enable_rls=True,
)
client = ScopedClient(backend=backend)
```

---

## TenantRouter

```python
from pyscoped.storage.tenant import TenantRouter
```

Multi-tenant storage router that maps tenant identifiers to isolated backend
instances. Each tenant gets its own database (or schema), preventing cross-tenant
data leakage.

### Constructor

```python
TenantRouter(
    tenant_resolver: Callable[[str], str],
    backend_factory: Callable[[str], StorageBackend],
    default_tenant_id: str | None = None,
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tenant_resolver` | `Callable[[str], str]` | *required* | Function that maps a request context (e.g. hostname, header value) to a tenant ID. |
| `backend_factory` | `Callable[[str], StorageBackend]` | *required* | Function that creates a `StorageBackend` for a given tenant ID. Called once per tenant; the result is cached. |
| `default_tenant_id` | `str \| None` | `None` | Fallback tenant ID when the resolver returns `None`. |

### Methods

#### provision_tenant

```python
async def provision_tenant(self, tenant_id: str) -> StorageBackend
```

Creates and initializes a backend for the given tenant. The backend is cached for
subsequent use. If the tenant already exists, the existing backend is returned.

| Parameter | Type | Description |
|---|---|---|
| `tenant_id` | `str` | Unique tenant identifier. |

#### list_tenants

```python
async def list_tenants(self) -> list[str]
```

Returns a list of all provisioned tenant IDs.

#### teardown_tenant

```python
async def teardown_tenant(self, tenant_id: str) -> None
```

Closes and removes the backend for the given tenant. The tenant's data is **not**
deleted from the underlying storage; this only releases the in-process resources.

| Parameter | Type | Description |
|---|---|---|
| `tenant_id` | `str` | The tenant to tear down. |

### Example

```python
from pyscoped.storage.tenant import TenantRouter
from pyscoped.storage.postgres import PostgresBackend

def resolve_tenant(hostname: str) -> str:
    # Map subdomain to tenant ID
    return hostname.split(".")[0]

def create_backend(tenant_id: str) -> PostgresBackend:
    return PostgresBackend(
        dsn=f"postgresql://scoped:secret@db:5432/tenant_{tenant_id}"
    )

router = TenantRouter(
    tenant_resolver=resolve_tenant,
    backend_factory=create_backend,
    default_tenant_id="default",
)

# Provision tenants
await router.provision_tenant("acme")
await router.provision_tenant("globex")

tenants = await router.list_tenants()
print(tenants)  # ["acme", "globex"]

# Use with ScopedClient
acme_backend = await router.provision_tenant("acme")
acme_client = ScopedClient(backend=acme_backend)

# Teardown
await router.teardown_tenant("globex")
```

---

## Migration System

```python
from pyscoped.storage.migrations import MigrationRunner
```

The migration system manages database schema evolution. Migrations are Python files
discovered by convention and applied in order.

### MigrationRunner

```python
MigrationRunner(backend: StorageBackend)
```

| Parameter | Type | Description |
|---|---|---|
| `backend` | `StorageBackend` | The backend to run migrations against. |

### Methods

#### discover

```python
async def discover(self, path: str | None = None) -> list[Migration]
```

Scans the migrations directory and returns all discovered migration files in order.
Built-in migrations are always included; custom migrations in the user-provided path
are appended.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `path` | `str \| None` | `None` | Additional directory to scan for custom migration files. |

#### Returns

A list of `Migration` objects sorted by version number.

#### apply_all

```python
async def apply_all(self) -> list[str]
```

Applies all pending (unapplied) migrations in order. Returns a list of applied
migration identifiers.

#### Returns

A list of migration ID strings that were applied.

#### Example

```python
runner = MigrationRunner(backend)
applied = await runner.apply_all()
print(f"Applied {len(applied)} migrations: {applied}")
# Applied 3 migrations: ['001_initial', '002_add_secrets', '003_add_rls']
```

#### rollback_last

```python
async def rollback_last(self) -> str | None
```

Rolls back the most recently applied migration. Returns the migration ID that was
rolled back, or `None` if no migrations are applied.

#### Returns

The rolled-back migration ID string, or `None`.

#### Example

```python
rolled_back = await runner.rollback_last()
if rolled_back:
    print(f"Rolled back: {rolled_back}")
else:
    print("Nothing to roll back")
```

#### get_status

```python
async def get_status(self) -> list[MigrationStatus]
```

Returns the status of all known migrations (applied and pending).

#### Returns

A list of `MigrationStatus` dataclasses.

```python
@dataclass(frozen=True)
class MigrationStatus:
    id: str
    name: str
    applied: bool
    applied_at: datetime | None
```

#### Example

```python
statuses = await runner.get_status()
for s in statuses:
    status_label = "applied" if s.applied else "pending"
    print(f"  [{status_label}] {s.id}: {s.name}")
# [applied] 001_initial: Create core tables
# [applied] 002_add_secrets: Add secrets and refs tables
# [pending] 003_add_rls: Add row-level security policies
```

---

## Complete Example

```python
import asyncio
from pyscoped import ScopedClient
from pyscoped.storage.sqlite import SQLiteBackend
from pyscoped.storage.postgres import PostgresBackend
from pyscoped.storage.migrations import MigrationRunner

async def main():
    # --- SQLite for development ---
    sqlite_backend = SQLiteBackend("/tmp/dev.db")
    await sqlite_backend.initialize()

    assert sqlite_backend.dialect == "sqlite"
    assert await sqlite_backend.table_exists("principals")

    async with sqlite_backend.transaction() as txn:
        await txn.execute(
            "INSERT INTO principals (id, display_name, kind) VALUES (?, ?, ?)",
            ("p-1", "Dev User", "user"),
        )
        row = await txn.fetch_one(
            "SELECT * FROM principals WHERE id = ?", ("p-1",)
        )
        print(row["display_name"])  # "Dev User"

    await sqlite_backend.close()

    # --- PostgreSQL for production ---
    pg_backend = PostgresBackend(
        dsn="postgresql://scoped:secret@localhost:5432/prod",
        pool_min_size=5,
        pool_max_size=20,
        enable_rls=True,
    )
    await pg_backend.initialize()

    # Check migration status
    runner = MigrationRunner(pg_backend)
    statuses = await runner.get_status()
    pending = [s for s in statuses if not s.applied]
    if pending:
        applied = await runner.apply_all()
        print(f"Applied {len(applied)} migrations")

    # Use with ScopedClient
    client = ScopedClient(backend=pg_backend)
    with client:
        admin = client.principals.create(display_name="Admin", kind="user")
        print(f"Created principal: {admin.id}")

    await pg_backend.close()

asyncio.run(main())
```
