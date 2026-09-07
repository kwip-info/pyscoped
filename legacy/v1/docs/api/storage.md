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
from scoped.storage import StorageBackend
```

The base class that all storage backends must implement.

```python
class StorageBackend(ABC):
    @property
    @abstractmethod
    def dialect(self) -> str: ...

    @abstractmethod
    def initialize(self) -> None: ...

    @abstractmethod
    def transaction(self) -> StorageTransaction: ...

    @abstractmethod
    def execute(self, query: str, params: tuple = ()) -> Any: ...

    @abstractmethod
    def fetch_one(self, query: str, params: tuple = ()) -> dict | None: ...

    @abstractmethod
    def fetch_all(self, query: str, params: tuple = ()) -> list[dict]: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def table_exists(self, table_name: str) -> bool: ...
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
| `SASQLiteBackend` | `"sqlite"` |
| `SAPostgresBackend` | `"postgresql"` |

#### initialize

```python
def initialize(self) -> None
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
with backend.transaction() as txn:
    txn.execute("INSERT INTO ...", (value,))
    row = txn.fetch_one("SELECT ...", (id,))
    txn.commit()
# auto-rolls back on exception
```

#### execute

```python
def execute(self, query: str, params: tuple = ()) -> Any
```

Executes a write query (INSERT, UPDATE, DELETE) outside of an explicit transaction.
The operation is auto-committed.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | *required* | SQL query string with `?` (SQLite) or `$1` (PostgreSQL) placeholders. |
| `params` | `tuple` | `()` | Bind parameters. |

#### fetch_one

```python
def fetch_one(self, query: str, params: tuple = ()) -> dict | None
```

Executes a read query and returns the first row as a dictionary, or `None` if no
rows match.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | *required* | SQL query string. |
| `params` | `tuple` | `()` | Bind parameters. |

#### fetch_all

```python
def fetch_all(self, query: str, params: tuple = ()) -> list[dict]
```

Executes a read query and returns all matching rows as a list of dictionaries.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | `str` | *required* | SQL query string. |
| `params` | `tuple` | `()` | Bind parameters. |

#### close

```python
def close(self) -> None
```

Releases all resources held by the backend (file handles, connection pools). The
backend must not be used after calling `close()`.

#### table_exists

```python
def table_exists(self, table_name: str) -> bool
```

Returns `True` if the specified table exists in the database.

| Parameter | Type | Description |
|---|---|---|
| `table_name` | `str` | The table name to check. |

---

## StorageTransaction

```python
from scoped.storage import StorageTransaction
```

A transaction context manager providing atomic, isolated database operations.

```python
class StorageTransaction:
    def execute(self, query: str, params: tuple = ()) -> Any: ...
    def execute_many(self, query: str, params_seq: list[tuple]) -> None: ...
    def fetch_one(self, query: str, params: tuple = ()) -> dict | None: ...
    def fetch_all(self, query: str, params: tuple = ()) -> list[dict]: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def __enter__(self) -> StorageTransaction: ...
    def __exit__(self, exc_type, exc_val, exc_tb) -> None: ...
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

When used as a context manager, the transaction auto-rolls back if an exception
propagates. Callers are responsible for explicit `commit()`.

```python
with backend.transaction() as txn:
    txn.execute(
        "INSERT INTO principals (id, display_name) VALUES (?, ?)",
        ("p-001", "Alice"),
    )
    result = txn.fetch_one(
        "SELECT * FROM principals WHERE id = ?", ("p-001",)
    )
    txn.commit()
```

---

## SQLiteBackend (deprecated)

> **Deprecated since 0.7.0.** Use `SASQLiteBackend` instead.

The legacy `SQLiteBackend` in `scoped.storage.sqlite` is still functional but emits
a deprecation warning on import. See `SASQLiteBackend` below for the recommended
replacement.

---

## PostgresBackend (deprecated)

> **Deprecated since 0.7.0.** Use `SAPostgresBackend` instead.

The legacy `PostgresBackend` in `scoped.storage.postgres` is still functional but
emits a deprecation warning on import. See `SAPostgresBackend` below for the
recommended replacement.

---

## SASQLiteBackend

```python
from scoped.storage.sa_sqlite import SASQLiteBackend
```

SQLAlchemy Core-backed SQLite storage. Drop-in replacement for `SQLiteBackend` that
uses SQLAlchemy for connection management, schema creation, and parameter binding.

### Constructor

```python
SASQLiteBackend(path: str = ":memory:")
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `path` | `str` | `":memory:"` | File path for the SQLite database. Use `":memory:"` for in-memory. |

### Key Differences from SQLiteBackend

- Schema created via `metadata.create_all(engine)` instead of inline DDL
- Uses `StaticPool` for in-memory databases (shared connection)
- Parameters rewritten from `?` to `:name` style via `_rewrite_sql_params()`
- Accepts both `?`-style and `dict`-style parameters

### Example

```python
backend = SASQLiteBackend(":memory:")
backend.initialize()

backend.execute(
    "INSERT INTO principals (id, kind, display_name) VALUES (?, ?, ?)",
    ("p-1", "user", "Alice"),
)
```

---

## SAPostgresBackend

```python
from scoped.storage.sa_postgres import SAPostgresBackend
```

SQLAlchemy Core-backed PostgreSQL storage with connection pooling and optional
row-level security. Drop-in replacement for `PostgresBackend`.

### Constructor

```python
SAPostgresBackend(
    dsn: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_timeout: float = 30.0,
    enable_rls: bool = False,
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `dsn` | `str` | *required* | PostgreSQL connection string. Automatically converts `postgresql://` to `postgresql+psycopg://`. |
| `pool_size` | `int` | `5` | Number of connections kept in the pool. |
| `max_overflow` | `int` | `10` | Extra connections beyond `pool_size` allowed. |
| `pool_timeout` | `float` | `30.0` | Seconds to wait for a connection. |
| `enable_rls` | `bool` | `False` | Enable row-level security context injection. |

### Example

```python
backend = SAPostgresBackend(
    "postgresql://user:pass@host/db",
    pool_size=10,
    enable_rls=True,
)
backend.initialize()
```

---

## SQLAlchemy Core Query Building

All 16 layers build queries using SQLAlchemy Core constructs instead of raw SQL
strings. The `compile_for()` bridge compiles them to the `(sql, params)` format
that `StorageBackend` accepts.

```python
import sqlalchemy as sa
from scoped.storage._schema import principals
from scoped.storage._query import compile_for

stmt = sa.select(principals).where(principals.c.id == "alice")
sql, params = compile_for(stmt, dialect="sqlite")
row = backend.fetch_one(sql, params)
```

For UPSERT (INSERT ... ON CONFLICT DO UPDATE):

```python
from scoped.storage._query import dialect_insert

stmt = dialect_insert(principals, "sqlite").values(id="p1", kind="user")
stmt = stmt.on_conflict_do_update(
    index_elements=["id"],
    set_={"kind": stmt.excluded.kind},
)
sql, params = compile_for(stmt, "sqlite")
backend.execute(sql, params)
```

Table definitions are in `scoped.storage._schema` (63 tables). The `dialect`
parameter should match `backend.dialect` (`"sqlite"`, `"postgres"`, or `"generic"`).

---

## TenantRouter

```python
from scoped.storage.tenant_router import TenantRouter
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
def provision_tenant(self, tenant_id: str) -> StorageBackend
```

Creates and initializes a backend for the given tenant. The backend is cached for
subsequent use. If the tenant already exists, the existing backend is returned.

| Parameter | Type | Description |
|---|---|---|
| `tenant_id` | `str` | Unique tenant identifier. |

#### list_tenants

```python
def list_tenants(self) -> list[str]
```

Returns a list of all provisioned tenant IDs.

#### teardown_tenant

```python
def teardown_tenant(self, tenant_id: str) -> None
```

Closes and removes the backend for the given tenant. The tenant's data is **not**
deleted from the underlying storage; this only releases the in-process resources.

| Parameter | Type | Description |
|---|---|---|
| `tenant_id` | `str` | The tenant to tear down. |

### Example

```python
from scoped.storage.tenant_router import TenantRouter
from scoped.storage.sa_postgres import SAPostgresBackend

def resolve_tenant(hostname: str) -> str:
    # Map subdomain to tenant ID
    return hostname.split(".")[0]

def create_backend(tenant_id: str) -> SAPostgresBackend:
    return SAPostgresBackend(
        f"postgresql://scoped:secret@db:5432/tenant_{tenant_id}"
    )

router = TenantRouter(
    tenant_resolver=resolve_tenant,
    backend_factory=create_backend,
    default_tenant_id="default",
)

# Provision tenants
router.provision_tenant("acme")
router.provision_tenant("globex")

tenants = router.list_tenants()
print(tenants)  # ["acme", "globex"]

# Use with ScopedClient
acme_backend = router.provision_tenant("acme")
acme_client = ScopedClient(backend=acme_backend)

# Teardown
router.teardown_tenant("globex")
```

---

## Migration System

```python
from scoped.storage.migrations import MigrationRunner
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
def discover(self, package_path: str | None = None) -> int
```

Scans the migrations directory and returns all discovered migration files in order.
Built-in migrations are always included; custom migrations in the user-provided path
are appended.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `package_path` | `str \| None` | `None` | Dotted Python package path to scan for custom migrations. |

#### Returns

The number of migrations discovered.

#### apply_all

```python
def apply_all(self) -> list[str]
```

Applies all pending (unapplied) migrations in order. Returns a list of applied
migration identifiers.

#### Returns

A list of migration ID strings that were applied.

#### Example

```python
runner = MigrationRunner(backend)
applied = runner.apply_all()
print(f"Applied {len(applied)} migrations: {applied}")
# Applied 3 migrations: ['001_initial', '002_add_secrets', '003_add_rls']
```

#### rollback_last

```python
def rollback_last(self) -> str | None
```

Rolls back the most recently applied migration. Returns the migration ID that was
rolled back, or `None` if no migrations are applied.

#### Returns

The rolled-back migration ID string, or `None`.

#### Example

```python
rolled_back = runner.rollback_last()
if rolled_back:
    print(f"Rolled back: {rolled_back}")
else:
    print("Nothing to roll back")
```

#### get_status

```python
def get_status(self) -> list[MigrationStatus]
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
statuses = runner.get_status()
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
from scoped.client import ScopedClient
from scoped.storage.sa_sqlite import SASQLiteBackend
from scoped.storage.sa_postgres import SAPostgresBackend
from scoped.storage.migrations import MigrationRunner

# --- SQLite for development ---
sqlite_backend = SASQLiteBackend("/tmp/dev.db")
sqlite_backend.initialize()

assert sqlite_backend.dialect == "sqlite"
assert sqlite_backend.table_exists("principals")

with sqlite_backend.transaction() as txn:
    txn.execute(
        "INSERT INTO principals (id, display_name, kind) VALUES (?, ?, ?)",
        ("p-1", "Dev User", "user"),
    )
    row = txn.fetch_one(
        "SELECT * FROM principals WHERE id = ?", ("p-1",)
    )
    print(row["display_name"])  # "Dev User"
    txn.commit()

sqlite_backend.close()

# --- PostgreSQL for production ---
pg_backend = SAPostgresBackend(
    "postgresql://scoped:secret@localhost:5432/prod",
    pool_size=10,
    enable_rls=True,
)
pg_backend.initialize()

# Check migration status
runner = MigrationRunner(pg_backend)
statuses = runner.get_status()
pending = [s for s in statuses if not s.applied]
if pending:
    applied = runner.apply_all()
    print(f"Applied {len(applied)} migrations")

# Use with ScopedClient
client = ScopedClient(backend=pg_backend)
with client.as_principal(admin):
    admin = client.principals.create(display_name="Admin", kind="user")
    print(f"Created principal: {admin.id}")

pg_backend.close()
```
