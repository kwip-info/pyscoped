# Changelog

## 0.6.0 (2026-04-01)

### Added
- **Entity update methods** — `PrincipalStore.update_principal()` and `ScopeLifecycle.update_scope()` for updating display names, descriptions, and metadata with audit trails. Metadata merges (additive, not replace). Exposed via `principals.update()` and `scopes.update()` namespaces
- **Bulk operations** — `ScopedManager.create_many()` for atomic batch object creation in a single transaction with batched audit entries. `ScopeLifecycle.add_members()` for adding multiple members at once. Exposed via `objects.create_many()` and `scopes.add_members()`
- **Rules enforcement** — `RuleEngine` wired into `ScopedServices` and injected into `ScopedManager`. DENY rules are now enforced before `create()`, `update()`, and `tombstone()` operations, raising `AccessDeniedError`. No-op when no rules are configured (backward compatible)
- **Paginated list_versions()** — accepts `limit` and `offset` parameters to avoid loading all version data into memory
- **Chunked verify_chain()** — processes audit entries in configurable `chunk_size` chunks (default 5000) instead of loading the entire trail. Maintains chain linkage across chunk boundaries
- **Django async middleware** — `ScopedContextMiddleware` now supports both sync and async views via `@sync_and_async_middleware` (Django 4.1+)
- **Django REST Framework integration** — new `scoped.contrib.django.rest_framework` module with `ScopedAuthentication` (resolves from resolver, header, or Django auth), `IsScopedPrincipal` and `HasScopeAccess` permission classes, and `ScopedUser` wrapper
- **FastAPI WebSocket support** — middleware handles `scope["type"] == "websocket"`, sets `ScopedContext` from handshake headers for the connection lifetime
- **Proper return type hints** — all namespace methods now return specific types (`Principal`, `Scope`, `ScopedObject`, `TraceEntry`, etc.) instead of `Any`, using `TYPE_CHECKING` guards to avoid circular imports
- **Structured logging** — new `scoped.logging` module with `ScopedLogger` (JSON structured output), `get_logger()` factory, auto-enrichment with principal_id from context, `SCOPED_LOG_LEVEL` env var
- **Extended OpenTelemetry** — `instrument()` now covers 21 operations: scope lifecycle (create, rename, update, add_member, revoke_member, freeze, archive, list), principal management (create, get, update, list), and rule evaluation, in addition to existing object CRUD, audit, and secret operations
- **Webhook HTTP transport** — `WebhookDelivery.http_transport` static method using stdlib `urllib.request` for production webhook delivery. Supports custom headers from endpoint config
- **Exponential backoff retries** — `retry_failed(backoff_base=60)` enforces delay between retry attempts: `backoff_base * 2^(attempt-1)` seconds. `backoff_base=0` disables for testing
- **Scheduler → JobQueue bridge** — `Scheduler.process_due_actions(queue)` enqueues all due actions, advances recurring schedules by interval, and archives one-shot actions
- **Connector federation transport** — `ConnectorManager` accepts a pluggable `transport` callable for HTTP push to remote endpoints. `sync_object()` now pushes data for outbound syncs, records `FAILED` traffic on transport errors. `ConnectorManager.http_transport` static method provided
- **Postgres Row-Level Security** — `PostgresBackend(enable_rls=True)` sets `app.current_principal_id` per-connection from `ScopedContext`. Migration m0013 creates RLS policies on 21+ tables with `FORCE ROW LEVEL SECURITY`. Uses `SET LOCAL` for transactions, `SET` + `RESET` for autocommit
- **Database-per-tenant isolation** — new `TenantRouter` storage backend routes operations to per-tenant databases based on `ScopedContext`. Thread-safe backend cache, tenant lifecycle management (`provision_tenant`, `teardown_tenant`, `list_tenants`)
- **Composite indexes** — migration m0012 adds `(scope_id, lifecycle)`, `(principal_id, lifecycle)`, and `(action, timestamp)` indexes for visibility JOINs and rate-limit queries
- **CLAUDE.md** — comprehensive LLM workspace context file (520 lines) covering full API surface, architecture, isolation model, and integration guides
- **Full documentation** — 21 new docs (9,100+ lines) across guides, API reference, integrations, features, and reference categories with `manifest.json` for platform export

### Changed
- **Recursive CTE hierarchy traversal** — `ancestor_scope_ids()`, `descendant_scope_ids()`, and `_visible_via_hierarchy()` rewritten from N+1 query loops to single `WITH RECURSIVE` queries (both SQLite and Postgres)
- **Thread-safe global client** — `scoped.init()` protected by `threading.Lock` to prevent race conditions on `_default_client`
- **Multi-process audit safety** — `AuditWriter` re-seeds sequence from database before each write to handle multi-process scenarios (e.g. gunicorn workers)
- **`inspect.isawaitable()`** — FastAPI middleware uses `inspect.isawaitable()` instead of `hasattr(result, "__await__")` for async principal resolver detection

## 0.5.0 (2026-04-01)

### Added
- **Scope rename** — `ScopeLifecycle.rename_scope()` and `client.scopes.rename()` for renaming scopes with full audit trail (before/after state via `SCOPE_MODIFY`). Validates scope is mutable (not frozen/archived)
- **Scope pagination** — `list_scopes()` now accepts `limit` and `offset` parameters for pagination. Previously returned all matching scopes with no limit
- **Scope count** — `ScopeLifecycle.count_scopes()` and `client.scopes.count()` for efficient scope counting without loading full rows
- **Order-by for scopes** — `list_scopes()` accepts `order_by` parameter with `-` prefix for descending (e.g. `"-name"`, `"created_at"`). Allowed columns: `created_at`, `name`
- **Order-by for objects** — `list_objects()` accepts `order_by` parameter. Allowed columns: `created_at`, `object_type`
- **Order-by for audit queries** — `AuditQuery.query()` accepts `order_by` parameter. Allowed columns: `sequence`, `timestamp`. Enables native descending queries (e.g. `"-sequence"` for most-recent-first) without client-side reversal

## 0.4.0 (2026-03-31)

### Added
- **Management plane contract** — 30 Pydantic models defining the complete API between SDK and hosted management plane: account provisioning, API key management, sync batches, billing/usage, and health checks. Both sides import from `scoped.sync.models` — zero contract drift
- **Sync agent** — `SyncAgent` background thread pushes audit metadata to the management plane. Full lifecycle: `start()`, `pause()`, `resume()`, `stop()`, `status()`, `verify()`. Watermark persisted in `_sync_state` table for crash recovery
- **Transport security** — HMAC-SHA256 signed batches with derived signing key, content hashes, chain hashes tying to the tamper-evident audit trail. 5-layer security: TLS, Bearer auth, HMAC signing, content hash, chain hash
- **`_sync_state` table** — migration m0011, colocated with user data for backup/restore. Tracks watermark position, sync status, error state with exponential backoff
- **Sync exceptions** — `SyncError`, `SyncNotConfiguredError`, `SyncTransportError`, `SyncAuthenticationError`, `SyncBatchRejectedError`, `SyncVerificationError`
- **`SyncConfig`** — configurable interval, batch size, retries, backoff, timeout
- **`SyncEntryMetadata`** — audit entry model that deliberately excludes `before_state`/`after_state`. Customer data never leaves their infrastructure
- **`ResourceCounts`** — active objects, principals, scopes snapshot for usage-based billing metering
- **Billing models** — `PlanLimits`, `UsageSnapshot`, `UsageHistoryResponse`, `PlanInfoResponse` for usage-based pricing
- **Account models** — `ProvisionRequest/Response`, `AccountInfo`, `ApiKeyMetadata`, key create/revoke/rotate models

### Changed
- **Pydantic is now a core dependency** (`pydantic>=2.0`). Required for the shared contract models between SDK and management plane
- **`ScopedClient.start_sync()`** — now creates a real `SyncAgent` instead of raising `NotImplementedError`. Requires `api_key`
- **`ScopedClient.sync_status()`** — returns `SyncStateSnapshot` dict when agent is active
- **`ScopedClient.close()`** — stops the sync agent if running
- SQLite and Postgres inline schemas include `_sync_state` table

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
