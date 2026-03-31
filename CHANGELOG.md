# Changelog

## 0.3.0 (2026-03-31)

### Added
- **Simplified SDK** — `scoped.init()` single entry point with URL-based backend selection (`sqlite:///`, `postgresql://`). After init, use `scoped.objects.create()`, `scoped.principals.create()`, etc. at module level
- **`ScopedClient`** — instance-based client with namespace proxies (`.objects`, `.principals`, `.scopes`, `.audit`, `.secrets`), context manager support, and `as_principal()` for setting the acting user
- **Context-aware defaults** — all namespace methods infer `principal_id`, `owner_id`, `granted_by`, `projected_by` from the active `ScopedContext` when not passed explicitly
- **Merged scopes namespace** — `client.scopes` unifies `ScopeLifecycle` and `ProjectionManager` into one API: `create()`, `add_member()`, `project()`, `unproject()`, `freeze()`, `archive()`
- **API key validation** — `psc_live_<32hex>` / `psc_test_<32hex>` format, validated on init, stored for future management plane sync
- **Sync method stubs** — `client.start_sync()`, `pause_sync()`, `resume_sync()`, `stop_sync()`, `sync_status()`, `verify_sync()` defined for the management plane (implementation in a future release)
- **Object/ID flexibility** — all namespace methods accept model objects or string IDs interchangeably
- **String enum acceptance** — pass `role="editor"` instead of importing `ScopeRole.EDITOR`

### Changed
- **Flask extension** — now accepts `SCOPED_DATABASE_URL` config key, creates a `ScopedClient` internally, sets the global default so `scoped.objects` works in route handlers
- **FastAPI middleware** — now accepts `database_url` and `api_key` constructor args, creates a `ScopedClient` internally, sets the global default
- **Django adapter** — `get_client()` creates a `ScopedClient` from `DjangoORMBackend` and sets the global default on `AppConfig.ready()`
- **MCP server** — `create_scoped_server()` accepts a `ScopedClient` directly; tools and resources use the namespace API instead of dict-based service access
- **OTel instrumentation** — `instrument()` now accepts both `ScopedClient` and `ScopedServices`
- **FastAPI dependencies** — `get_services()` kept for backward compat; new `get_client()` dependency added

## 0.2.0 (2026-03-31)

### Added
- **PostgreSQL backend** — production-grade `PostgresBackend` with psycopg v3 connection pooling, full schema DDL, and `tsvector` full-text search. Install with `pip install pyscoped[postgres]`
- **AWS KMS encryption backend** — `AWSKMSBackend` for Layer 11 secrets, server-side encrypt/decrypt via AWS Key Management Service
- **GCP Cloud KMS encryption backend** — `GCPKMSBackend` for Layer 11 secrets via Google Cloud KMS
- **S3 blob storage** — `S3BlobBackend` for Extension A4, stores binary content in Amazon S3 with sharded key layout
- **GCS blob storage** — `GCSBlobBackend` for Extension A4 via Google Cloud Storage
- **Automatic secret rotation** — `make_rotation_executor()`, `schedule_auto_rotations()`, and `run_pending_rotations()` wire Layer 11 policies to Layer 16 scheduling
- **OpenTelemetry instrumentation** — `instrument(services)` wraps object CRUD, audit recording, and secret operations with OTel spans. Silent no-op when `opentelemetry-api` is not installed
- **Testing utilities for downstream users** — `ScopedFactory` for quick test data creation, 7 domain-specific assertion helpers (`assert_isolated`, `assert_visible`, `assert_audit_recorded`, etc.), importable pytest fixtures (`scoped_services`, `alice`, `bob`, `sample_object`, `sample_scope`)
- **`dialect` property on `StorageBackend`** — returns `"sqlite"`, `"postgres"`, or `"generic"` for backend-aware code
- **Search strategy abstraction** — `SQLiteFTS5Strategy` and `PostgresFTSStrategy` for pluggable full-text search backends
- **Migration dialect support** — migrations 0001, 0005, 0007 branch on `backend.dialect` for Postgres-compatible DDL
- Optional dependency extras: `pyscoped[postgres]`, `pyscoped[aws]`, `pyscoped[gcp]`, `pyscoped[otel]`

### Changed
- `INSERT OR REPLACE` in registry store replaced with portable `ON CONFLICT DO UPDATE` syntax (works on both SQLite 3.24+ and Postgres 9.5+)
- Django ORM backend now imports `translate_placeholders` from shared `scoped.storage._sql_utils` module
- Health checker is dialect-aware — uses `information_schema` on Postgres instead of `sqlite_master`
- Flask extension supports `SCOPED_STORAGE_BACKEND = "postgres"` with `SCOPED_POSTGRES_DSN` config
- `ScopedConfig` gains `postgres_dsn` field

## 0.1.3 (2026-03-31)

### Fixed
- All documentation links now point to correct GitHub repository (kwip-info/pyscoped)
- Clean repository structure — library-only, no application code

## 0.1.1 (2026-03-31)

### Fixed
- ScopedManager now initialized with audit_writer for proper hash-chained audit trails
- Workflow transitions use ScopedManager.update() instead of raw SQL for correct versioning
- Removed duplicate audit entries from runtime data API
- Import pipeline resolves principal FK constraints by importing identities first
- Scope and object creation APIs resolve 'system' owner to real principal for FK compliance

### Added
- Runtime workflow engine: state machine transitions with validation
- Compliance test suite: 16 tests covering versioning, audit integrity, isolation, and rule enforcement
- Scope filtering on data list and widget APIs

### Documentation
- Comprehensive PyPI README with correct code examples
- Getting Started guide with end-to-end walkthrough
- API Reference covering all 16 layers
- Framework Adapters guide (Django, FastAPI, Flask, MCP)

## 0.1.0 (2026-03-28)

### Added
- Initial release
- 16 layers: Registry through Scheduling
- 9 extensions: Migrations through Import/Export
- 4 framework adapters: Django, FastAPI, Flask, MCP
- Compliance engine with 17+ invariant checks
- 1,493 tests
