# Changelog

## 0.9.1 (2026-04-02)

### Security
- **Membership expiration enforcement** — `scope_memberships.expires_at` is now checked in all 6 visibility query sites: `is_member()`, `can_see()`, `scope_member_ids()`, `get_memberships()`, `get_principal_scopes()`, `_projected_object_ids()`. Expired memberships are lazily archived on access. New `ScopeMembership.is_expired` property and `active_membership_condition()` helper
- **Projection access level enforcement** — `AccessLevel` (READ/WRITE/ADMIN) stored on projections is now enforced: `update()` requires WRITE, `tombstone()` requires ADMIN. Opt-in via `visibility_engine` parameter on `ScopedManager`. Owners always have full access regardless of projection
- **Webhook secrets encryption** — `config_json` sensitive fields (`headers`, `auth_token`, `secret`) are Fernet-encrypted at rest. `encrypt_config()` / `decrypt_config()` in `scoped.events.crypto`. Backward compatible with existing plaintext configs

## 0.9.0 (2026-04-02)

### Added
- **Pagination on all unbounded queries** — `Registry.by_kind()`, `by_namespace()`, `by_tag()`, `by_lifecycle()`, `all()`, `query()` now accept `limit`/`offset`. `ScopeLifecycle.list_scopes()` default changed from `None` to `1000`. `RollbackExecutor._collect_descendants()` bounded by `max_depth=100`
- **Rule engine caching** — `RuleEngine(cache_ttl=60.0)` enables TTL-based in-memory cache for rule lookups. `RuleCache` is thread-safe with `get()`/`put()`/`invalidate()`/`stats()`. Shared between `RuleStore` and `RuleEngine` via `ScopedServices` wiring. Cache invalidated on all mutations (create/update/archive/bind/unbind)
- **Audit chain optimization** — `verify_chain()` now selects only 9 hash-relevant columns instead of `SELECT *`, eliminating I/O for `before_state`/`after_state`/`metadata_json`. New `VerificationEntry` lightweight dataclass. `AuditNamespace.verify(chunk_size=5000)` parameter exposed
- **Audit retention** — `AuditRetention` class with `apply(policy)`, `estimate(policy)`, `compact()`. `RetentionPolicy` supports `max_age_days`, `max_entries`, `compact_before_state`, `compact_after_state`. Compaction nulls state columns while preserving hash chain integrity

## 0.8.1 (2026-04-02)

### Added
- **Rollback preview (dry-run)** — `rollback_action()`, `rollback_to_timestamp()`, and `rollback_cascade()` now accept `dry_run=True`, returning a `RollbackPreview` with `would_rollback`, `would_deny`, and `entry_count` without modifying any data
- **Rule evaluation debugging** — `RuleEngine.evaluate_with_explanation()` returns `EvaluationExplanation` with per-rule `RuleExplanation` (condition matches, binding info, human-readable reason) and a summary string. New models: `ConditionMatch`, `RuleExplanation`, `EvaluationExplanation`
- **Namespace API completeness** — `PrincipalsNamespace`: `archive()`, `add_relationship()`, `relationships()`, `list()` with `limit`/`offset`. `ScopesNamespace`: `children()`, `ancestors()`, `descendants()`, `path()` hierarchy traversal, `members()`/`projections()` with `limit`/`offset`. `AuditNamespace`: `count()`, `export(format="json"|"csv")`
- **Service-layer pagination** — `PrincipalStore.list_principals()`, `ScopeLifecycle.get_memberships()`, `get_principal_scopes()`, `ProjectionManager.get_projections()` now accept `limit`/`offset`

## 0.8.0 (2026-04-02)

### Added
- **Typed IDs** — `scoped.ids` module with 13 thin `str` subclasses: `PrincipalId`, `ObjectId`, `ScopeId`, `RuleId`, `TraceId`, `SecretId`, `VersionId`, `BindingId`, `MembershipId`, `ProjectionId`, `ConnectorId`, `ScheduleId`, `JobId`. All are `isinstance(x, str)` — zero breakage. `PrincipalId.generate()` replaces `generate_id()` for typed ID creation. Re-exported from `scoped.types`
- **Typed rule conditions** — `scoped.rules.conditions` module with Pydantic models for each rule type: `AccessCondition`, `RateLimitCondition`, `QuotaCondition`, `RedactionCondition`, `FeatureFlagCondition`. Validated at creation time (not evaluation time). `Rule.typed_conditions` property for typed access. `RuleStore.create_rule()` accepts typed models or raw dicts
- **Enum coercion helpers** — `coerce_role()` and `coerce_access_level()` in `scoped.tenancy.models` validate string inputs with descriptive `ValueError` messages. Namespace APIs now accept `str | ScopeRole` and `str | AccessLevel`
- **`Scope.lifecycle_display`** property — returns `"FROZEN"` instead of the internal `"DEPRECATED"` name

## 0.7.1 (2026-04-02)

### Changed
- **Default backends switched** — `scoped.init()` and `ScopedClient(database_url=...)` now create `SASQLiteBackend`/`SAPostgresBackend` instead of the legacy backends
- **`StorageBackend.engine` property** — Optional property returning the SQLAlchemy engine for SA-backed backends (`None` for legacy backends)

### Deprecated
- **`SQLiteBackend`** — Use `SASQLiteBackend` instead. Emits `DeprecationWarning` on construction. Will be removed in v1.0
- **`PostgresBackend`** — Use `SAPostgresBackend` instead. Emits `DeprecationWarning` on construction. Will be removed in v1.0

## 0.7.0 (2026-04-02)

### Added
- **SQLAlchemy Core query layer** — All 462 raw SQL strings across 58 consumer files converted to `sa.select()`, `sa.insert()`, `sa.update()`, `sa.delete()` constructs compiled via `compile_for(stmt, dialect)`. Queries are dialect-portable across SQLite and PostgreSQL. Raw SQL preserved only for FTS5/tsvector full-text search and dynamic quota table queries with allowlist validation
- **SQLAlchemy Core schema** — `scoped.storage._schema` defines 63 `sa.Table` objects matching all DDL from migrations m0001–m0014 plus `scoped_migrations`. Used for query building only (not DDL)
- **Query compilation bridge** — `scoped.storage._query.compile_for(stmt, dialect)` compiles SQLAlchemy Core statements to `(sql, params)` tuples compatible with the existing `StorageBackend.execute/fetch_*` interface. Supports `render_postcompile=True` for IN clause expansion. `dialect_insert()` helper for dialect-aware UPSERT via `on_conflict_do_update()`
- **SASQLiteBackend** — `scoped.storage.sa_sqlite.SASQLiteBackend` — SQLAlchemy Core-backed SQLite storage, drop-in replacement for `SQLiteBackend`. Uses `StaticPool` for in-memory, `metadata.create_all()` for schema, DBAPI `executescript()` for scripts. Pragma setting via SA event listener
- **SAPostgresBackend** — `scoped.storage.sa_postgres.SAPostgresBackend` — SQLAlchemy Core-backed PostgreSQL storage with connection pooling (`QueuePool`), RLS support via `SET [LOCAL] app.current_principal_id`, drop-in replacement for `PostgresBackend`
- **Typed Object Protocol** — `scoped.register_type("invoice", InvoiceModel)` registers Pydantic models, dataclasses, or `ScopedSerializable` protocol types. `ScopedManager.create()` and `update()` auto-serialize typed instances. `ObjectVersion.typed_data` property auto-deserializes back to the registered type
- **Type adapters** — `scoped._type_adapters` with `PydanticAdapter` (`model_dump`/`model_validate`), `DataclassAdapter` (`asdict`/`cls(**data)`), `ScopedSerializableAdapter` (protocol methods), auto-detection in `TypeRegistry.register()`
- **`ScopedSerializable` protocol** — `scoped.types.ScopedSerializable` with `to_scoped_dict()` and `from_scoped_dict()` for custom serialization
- **Stability markers** — `@experimental()`, `@preview()`, `@stable()` decorators in `scoped._stability`. Emit `ExperimentalAPIWarning` or `PreviewAPIWarning` (both `FutureWarning` subclasses) on first use. Suppressible via `warnings.filterwarnings("ignore", category=...)`. `get_stability_level()` introspection helper
- **Django ScopedModel** — `scoped.contrib.django.models.ScopedModel` abstract base for Django models that auto-sync with pyscoped's object layer. `save()` creates/updates ScopedObjects atomically. `delete()` tombstones. `_to_scoped_dict()` handles DateTimeField, DecimalField, UUIDField, ForeignKey serialization. `ScopedMeta.scoped_fields` controls which fields sync
- **Django ScopedQuerySet** — `ScopedQuerySet.for_principal(principal_id)` filters by pyscoped visibility (owner + scope projections), falls back to `scoped_owner_id` filtering when no client
- **Django ScopedDjangoManager** — Secondary manager on `ScopedModel` with `for_principal()` shortcut. Default `objects` manager untouched
- **`scoped_context_for()` helper** — Context manager for non-HTTP code (management commands, Celery tasks) that sets `ScopedContext` from a principal ID
- **DjangoORMBackend dialect** — `dialect` property now returns the actual Django connection vendor (`"sqlite"` or `"postgres"`) instead of `"generic"`

### Changed
- **`sqlalchemy>=2.0` dependency** — Added to `pyproject.toml`. All storage consumers now build queries via SQLAlchemy Core instead of string interpolation
- **Stability decorations** — 21 classes marked `@experimental` (Layers 8-16), 4 classes marked `@preview` (Layer 13 connector/marketplace)

## 0.6.2 (2026-04-02)

### Security
- **SQL injection fix** — `QuotaChecker` now validates `count_column` and `scope_column` against an allowlist (`_ALLOWED_COLUMNS`) before interpolating into SQL. Previously only table names were validated. Also fixes `table == "objects"` comparison (should be `"scoped_objects"`)
- **Webhook config** — noted as requiring encryption at rest (tracked for follow-up)

### Fixed
- **Registry thread safety** — all 13 read methods now acquire `RLock`. Listener callbacks execute outside the lock to prevent deadlocks. `archive()` performs index cleanup atomically under a single lock hold. `CustomKind._registered` is now protected by a module-level `threading.Lock`
- **Transaction boundaries** — `archive_scope()`, `freeze_scope()`, `_add_membership()`, `update()`, and `tombstone()` now wrap multi-step operations in explicit database transactions. Audit entries are recorded after the business transaction commits
- **Audit sequence collisions** — new migration **m0014** adds a `UNIQUE` constraint on `audit_trail.sequence`. `AuditWriter.record()` now uses a database transaction with bounded retry (3 attempts) on sequence collision. `record_batch()` saves and restores in-memory state on transaction failure. New `AuditSequenceCollisionError` exception
- **Notification preferences** — replaced indirect `datetime_fromisoformat()` helper with direct `datetime.fromisoformat()` call
- **Event-notification pipeline** — `NotificationEngine.process_event()` is now automatically wired as a wildcard listener on `EventBus` when `ScopedServices.notifications` is accessed. Previously the pipeline was disconnected

### Added
- **Quota enforcement in write path** — `ScopedManager` accepts optional `quota_checker` and `rate_limit_checker`. Quota checks run inside the write transaction (TOCTOU-safe). Rate limit checks run before the transaction (approximate, acceptable for soft limits). New `QuotaChecker.check_in_txn()` method
- **Wildcard event listeners** — `EventBus.on_any(listener)` and `off_any(listener)` register listeners that receive all event types
- **Pluggable cron parser** — `Scheduler` accepts an optional `cron_parser: Callable[[str, datetime], datetime]` for real cron expression evaluation. Without it, cron schedules fall back to a 1-hour placeholder with a `warnings.warn()`

## 0.6.1 (2026-04-02)

### Added
- **Integration smoke test** — `scoped.testing.integration.PlatformSmokeTest` exercises the full SDK → Platform round-trip: object CRUD, audit chain, sync batch push, chain verification, usage reporting, and key listing. Runnable via `python -m scoped.testing.integration --base-url ... --api-key ...`

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
