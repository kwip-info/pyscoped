"""PostgreSQL storage backend using psycopg v3.

Production-grade backend with connection pooling, full ACID transactions,
and native tsvector full-text search. Requires ``psycopg[binary]`` and
``psycopg_pool`` — install via ``pip install pyscoped[postgres]``.
"""

from __future__ import annotations

from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
except ImportError as exc:
    raise ImportError(
        "PostgreSQL backend requires psycopg and psycopg_pool. "
        "Install with: pip install pyscoped[postgres]"
    ) from exc

from scoped.storage._sql_utils import translate_placeholders
from scoped.storage.interface import StorageBackend, StorageTransaction


class PostgresTransaction(StorageTransaction):
    """Transaction backed by a psycopg connection acquired from the pool."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn
        self._conn.execute("BEGIN")

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> Any:
        sql = translate_placeholders(sql)
        self._conn.execute(sql, params)
        return None

    def execute_many(self, sql: str, params_seq: list[tuple[Any, ...]]) -> None:
        sql = translate_placeholders(sql)
        cur = self._conn.cursor()
        for params in params_seq:
            cur.execute(sql, params)

    def fetch_one(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> dict[str, Any] | None:
        sql = translate_placeholders(sql)
        cur = self._conn.execute(sql, params)
        row = cur.fetchone()
        if row is None:
            return None
        columns = [desc.name for desc in cur.description]
        return dict(zip(columns, row))

    def fetch_all(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> list[dict[str, Any]]:
        sql = translate_placeholders(sql)
        cur = self._conn.execute(sql, params)
        rows = cur.fetchall()
        if not rows:
            return []
        columns = [desc.name for desc in cur.description]
        return [dict(zip(columns, row)) for row in rows]

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()


class PostgresBackend(StorageBackend):
    """PostgreSQL storage backend with connection pooling.

    Args:
        dsn: PostgreSQL connection string
             (e.g. ``"postgresql://user:pass@localhost/mydb"``).
        pool_min_size: Minimum connections kept open in the pool.
        pool_max_size: Maximum connections the pool will create.
        pool_timeout: Seconds to wait for a connection from the pool.
        pool_kwargs: Extra keyword arguments forwarded to
                     ``psycopg_pool.ConnectionPool``.
    """

    def __init__(
        self,
        dsn: str,
        *,
        pool_min_size: int = 2,
        pool_max_size: int = 10,
        pool_timeout: float = 30.0,
        pool_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._dsn = dsn
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool_timeout = pool_timeout
        self._pool_kwargs = pool_kwargs or {}
        self._pool: ConnectionPool | None = None

    @property
    def dialect(self) -> str:
        return "postgres"

    @property
    def pool(self) -> ConnectionPool:
        if self._pool is None:
            raise RuntimeError("PostgresBackend not initialized — call initialize() first")
        return self._pool

    def initialize(self) -> None:
        self._pool = ConnectionPool(
            self._dsn,
            min_size=self._pool_min_size,
            max_size=self._pool_max_size,
            timeout=self._pool_timeout,
            kwargs={"autocommit": True},
            **self._pool_kwargs,
        )
        self._pool.wait()
        self._create_schema()

    def _create_schema(self) -> None:
        """Create all framework tables."""
        with self.pool.connection() as conn:
            conn.autocommit = False
            try:
                for statement in SCHEMA_SQL.split(";"):
                    stmt = statement.strip()
                    if stmt and not stmt.startswith("--"):
                        conn.execute(stmt)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.autocommit = True

    def transaction(self) -> PostgresTransaction:
        conn = self.pool.getconn()
        conn.autocommit = False
        return _PoolReturningTransaction(conn, self.pool)

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> Any:
        sql = translate_placeholders(sql)
        with self.pool.connection() as conn:
            conn.execute(sql, params)
        return None

    def fetch_one(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> dict[str, Any] | None:
        sql = translate_placeholders(sql)
        with self.pool.connection() as conn:
            cur = conn.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                return None
            columns = [desc.name for desc in cur.description]
            return dict(zip(columns, row))

    def fetch_all(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> list[dict[str, Any]]:
        sql = translate_placeholders(sql)
        with self.pool.connection() as conn:
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                return []
            columns = [desc.name for desc in cur.description]
            return [dict(zip(columns, row)) for row in rows]

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def table_exists(self, table_name: str) -> bool:
        row = self.fetch_one(
            "SELECT 1 AS ok FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ?",
            (table_name,),
        )
        return row is not None


class _PoolReturningTransaction(PostgresTransaction):
    """Transaction that returns its connection to the pool on commit/rollback."""

    def __init__(self, conn: psycopg.Connection, pool: ConnectionPool) -> None:
        self._pool_ref = pool
        super().__init__(conn)

    def commit(self) -> None:
        try:
            self._conn.commit()
        finally:
            self._conn.autocommit = True
            self._pool_ref.putconn(self._conn)

    def rollback(self) -> None:
        try:
            self._conn.rollback()
        finally:
            self._conn.autocommit = True
            self._pool_ref.putconn(self._conn)

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        if exc_type is not None:
            self.rollback()


# =========================================================================
# Schema DDL — identical to SQLite except:
#   • No FTS5 virtual table (Postgres uses tsvector column + GIN index)
#   • BLOB → BYTEA for glacial_archives.compressed_data
# =========================================================================

SCHEMA_SQL = """
-- =====================================================================
-- REGISTRY
-- =====================================================================

CREATE TABLE IF NOT EXISTS registry_entries (
    id              TEXT PRIMARY KEY,
    urn             TEXT NOT NULL UNIQUE,
    kind            TEXT NOT NULL,
    namespace       TEXT NOT NULL,
    name            TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    registered_at   TEXT NOT NULL,
    registered_by   TEXT NOT NULL DEFAULT 'system',
    entry_version   INTEGER NOT NULL DEFAULT 1,
    previous_entry_id TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    tags_json       TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_registry_urn ON registry_entries(urn);
CREATE INDEX IF NOT EXISTS idx_registry_kind ON registry_entries(kind);
CREATE INDEX IF NOT EXISTS idx_registry_namespace ON registry_entries(namespace);
CREATE INDEX IF NOT EXISTS idx_registry_lifecycle ON registry_entries(lifecycle);


-- =====================================================================
-- PRINCIPALS (generic — application defines kinds)
-- =====================================================================

CREATE TABLE IF NOT EXISTS principals (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    display_name    TEXT NOT NULL DEFAULT '',
    registry_entry_id TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL DEFAULT 'system',
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (registry_entry_id) REFERENCES registry_entries(id)
);

CREATE INDEX IF NOT EXISTS idx_principals_kind ON principals(kind);


CREATE TABLE IF NOT EXISTS principal_relationships (
    id              TEXT PRIMARY KEY,
    parent_id       TEXT NOT NULL,
    child_id        TEXT NOT NULL,
    relationship    TEXT NOT NULL DEFAULT 'member_of',
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (parent_id) REFERENCES principals(id),
    FOREIGN KEY (child_id) REFERENCES principals(id),
    UNIQUE(parent_id, child_id, relationship)
);

CREATE INDEX IF NOT EXISTS idx_principal_rel_parent ON principal_relationships(parent_id);
CREATE INDEX IF NOT EXISTS idx_principal_rel_child ON principal_relationships(child_id);


-- =====================================================================
-- SCOPED OBJECTS (versioned, isolated)
-- =====================================================================

CREATE TABLE IF NOT EXISTS scoped_objects (
    id              TEXT PRIMARY KEY,
    object_type     TEXT NOT NULL,
    owner_id        TEXT NOT NULL,
    registry_entry_id TEXT,
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id)
);

CREATE INDEX IF NOT EXISTS idx_objects_owner ON scoped_objects(owner_id);
CREATE INDEX IF NOT EXISTS idx_objects_type ON scoped_objects(object_type);


CREATE TABLE IF NOT EXISTS object_versions (
    id              TEXT PRIMARY KEY,
    object_id       TEXT NOT NULL,
    version         INTEGER NOT NULL,
    data_json       TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    change_reason   TEXT NOT NULL DEFAULT '',
    checksum        TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (object_id) REFERENCES scoped_objects(id),
    UNIQUE(object_id, version)
);

CREATE INDEX IF NOT EXISTS idx_versions_object ON object_versions(object_id);
CREATE INDEX IF NOT EXISTS idx_versions_created ON object_versions(created_at);


CREATE TABLE IF NOT EXISTS tombstones (
    id              TEXT PRIMARY KEY,
    object_id       TEXT NOT NULL UNIQUE,
    tombstoned_at   TEXT NOT NULL,
    tombstoned_by   TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (object_id) REFERENCES scoped_objects(id)
);


-- =====================================================================
-- SCOPES (tenancy / sharing containers)
-- =====================================================================

CREATE TABLE IF NOT EXISTS scopes (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    owner_id        TEXT NOT NULL,
    parent_scope_id TEXT,
    registry_entry_id TEXT,
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (owner_id) REFERENCES principals(id),
    FOREIGN KEY (parent_scope_id) REFERENCES scopes(id)
);

CREATE INDEX IF NOT EXISTS idx_scopes_owner ON scopes(owner_id);
CREATE INDEX IF NOT EXISTS idx_scopes_parent ON scopes(parent_scope_id);


CREATE TABLE IF NOT EXISTS scope_memberships (
    id              TEXT PRIMARY KEY,
    scope_id        TEXT NOT NULL,
    principal_id    TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'viewer',
    granted_at      TEXT NOT NULL,
    granted_by      TEXT NOT NULL,
    expires_at      TEXT,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (scope_id) REFERENCES scopes(id),
    FOREIGN KEY (principal_id) REFERENCES principals(id),
    UNIQUE(scope_id, principal_id, role)
);

CREATE INDEX IF NOT EXISTS idx_memberships_scope ON scope_memberships(scope_id);
CREATE INDEX IF NOT EXISTS idx_memberships_principal ON scope_memberships(principal_id);


CREATE TABLE IF NOT EXISTS scope_projections (
    id              TEXT PRIMARY KEY,
    scope_id        TEXT NOT NULL,
    object_id       TEXT NOT NULL,
    projected_at    TEXT NOT NULL,
    projected_by    TEXT NOT NULL,
    access_level    TEXT NOT NULL DEFAULT 'read',
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (scope_id) REFERENCES scopes(id),
    FOREIGN KEY (object_id) REFERENCES scoped_objects(id),
    UNIQUE(scope_id, object_id)
);

CREATE INDEX IF NOT EXISTS idx_projections_scope ON scope_projections(scope_id);
CREATE INDEX IF NOT EXISTS idx_projections_object ON scope_projections(object_id);


-- =====================================================================
-- RULES
-- =====================================================================

CREATE TABLE IF NOT EXISTS rules (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    rule_type       TEXT NOT NULL,
    effect          TEXT NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 0,
    conditions_json TEXT NOT NULL DEFAULT '{}',
    registry_entry_id TEXT,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    current_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_rules_type ON rules(rule_type);
CREATE INDEX IF NOT EXISTS idx_rules_effect ON rules(effect);


CREATE TABLE IF NOT EXISTS rule_versions (
    id              TEXT PRIMARY KEY,
    rule_id         TEXT NOT NULL,
    version         INTEGER NOT NULL,
    conditions_json TEXT NOT NULL,
    effect          TEXT NOT NULL,
    priority        INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    change_reason   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (rule_id) REFERENCES rules(id),
    UNIQUE(rule_id, version)
);


CREATE TABLE IF NOT EXISTS rule_bindings (
    id              TEXT PRIMARY KEY,
    rule_id         TEXT NOT NULL,
    target_type     TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    bound_at        TEXT NOT NULL,
    bound_by        TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (rule_id) REFERENCES rules(id),
    UNIQUE(rule_id, target_type, target_id)
);

CREATE INDEX IF NOT EXISTS idx_bindings_rule ON rule_bindings(rule_id);
CREATE INDEX IF NOT EXISTS idx_bindings_target ON rule_bindings(target_type, target_id);


-- =====================================================================
-- AUDIT TRAIL (immutable, hash-chained)
-- =====================================================================

CREATE TABLE IF NOT EXISTS audit_trail (
    id              TEXT PRIMARY KEY,
    sequence        INTEGER NOT NULL,
    actor_id        TEXT NOT NULL,
    action          TEXT NOT NULL,
    target_type     TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    scope_id        TEXT,
    timestamp       TEXT NOT NULL,
    before_state    TEXT,
    after_state     TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    parent_trace_id TEXT,
    hash            TEXT NOT NULL,
    previous_hash   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (parent_trace_id) REFERENCES audit_trail(id)
);

CREATE INDEX IF NOT EXISTS idx_audit_sequence ON audit_trail(sequence);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_trail(actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_trail(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_trail(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_trail(action);
CREATE INDEX IF NOT EXISTS idx_audit_scope ON audit_trail(scope_id);


-- =====================================================================
-- ENVIRONMENTS (ephemeral & persistent workspaces)
-- =====================================================================

CREATE TABLE IF NOT EXISTS environments (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    owner_id        TEXT NOT NULL,
    template_id     TEXT,
    scope_id        TEXT,
    state           TEXT NOT NULL DEFAULT 'spawning',
    ephemeral       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    completed_at    TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (owner_id) REFERENCES principals(id),
    FOREIGN KEY (template_id) REFERENCES environment_templates(id),
    FOREIGN KEY (scope_id) REFERENCES scopes(id)
);

CREATE INDEX IF NOT EXISTS idx_env_owner ON environments(owner_id);
CREATE INDEX IF NOT EXISTS idx_env_state ON environments(state);
CREATE INDEX IF NOT EXISTS idx_env_ephemeral ON environments(ephemeral);


CREATE TABLE IF NOT EXISTS environment_templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    owner_id        TEXT NOT NULL,
    config_json     TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id)
);


CREATE TABLE IF NOT EXISTS environment_snapshots (
    id              TEXT PRIMARY KEY,
    environment_id  TEXT NOT NULL,
    name            TEXT NOT NULL DEFAULT '',
    snapshot_data   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    checksum        TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (environment_id) REFERENCES environments(id)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_env ON environment_snapshots(environment_id);


CREATE TABLE IF NOT EXISTS environment_objects (
    id              TEXT PRIMARY KEY,
    environment_id  TEXT NOT NULL,
    object_id       TEXT NOT NULL,
    origin          TEXT NOT NULL DEFAULT 'created',
    added_at        TEXT NOT NULL,
    FOREIGN KEY (environment_id) REFERENCES environments(id),
    FOREIGN KEY (object_id) REFERENCES scoped_objects(id),
    UNIQUE(environment_id, object_id)
);

CREATE INDEX IF NOT EXISTS idx_env_objects_env ON environment_objects(environment_id);


-- =====================================================================
-- STAGES & FLOW (pipelines, channels, promotions)
-- =====================================================================

CREATE TABLE IF NOT EXISTS stages (
    id              TEXT PRIMARY KEY,
    pipeline_id     TEXT NOT NULL,
    name            TEXT NOT NULL,
    ordinal         INTEGER NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    UNIQUE(pipeline_id, name)
);

CREATE TABLE IF NOT EXISTS pipelines (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    owner_id        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id)
);


CREATE TABLE IF NOT EXISTS stage_transitions (
    id              TEXT PRIMARY KEY,
    object_id       TEXT NOT NULL,
    from_stage_id   TEXT,
    to_stage_id     TEXT NOT NULL,
    transitioned_at TEXT NOT NULL,
    transitioned_by TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (object_id) REFERENCES scoped_objects(id),
    FOREIGN KEY (from_stage_id) REFERENCES stages(id),
    FOREIGN KEY (to_stage_id) REFERENCES stages(id)
);

CREATE INDEX IF NOT EXISTS idx_transitions_object ON stage_transitions(object_id);
CREATE INDEX IF NOT EXISTS idx_transitions_to ON stage_transitions(to_stage_id);


CREATE TABLE IF NOT EXISTS flow_channels (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    target_type     TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    allowed_types   TEXT NOT NULL DEFAULT '[]',
    owner_id        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id)
);

CREATE INDEX IF NOT EXISTS idx_flow_source ON flow_channels(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_flow_target ON flow_channels(target_type, target_id);


CREATE TABLE IF NOT EXISTS promotions (
    id              TEXT PRIMARY KEY,
    object_id       TEXT NOT NULL,
    source_env_id   TEXT NOT NULL,
    target_scope_id TEXT NOT NULL,
    target_stage_id TEXT,
    promoted_at     TEXT NOT NULL,
    promoted_by     TEXT NOT NULL,
    FOREIGN KEY (object_id) REFERENCES scoped_objects(id),
    FOREIGN KEY (source_env_id) REFERENCES environments(id),
    FOREIGN KEY (target_scope_id) REFERENCES scopes(id),
    FOREIGN KEY (target_stage_id) REFERENCES stages(id)
);

CREATE INDEX IF NOT EXISTS idx_promotions_env ON promotions(source_env_id);
CREATE INDEX IF NOT EXISTS idx_promotions_scope ON promotions(target_scope_id);


-- =====================================================================
-- DEPLOYMENTS (graduation to external targets)
-- =====================================================================

CREATE TABLE IF NOT EXISTS deployment_targets (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    target_type     TEXT NOT NULL,
    config_json     TEXT NOT NULL DEFAULT '{}',
    owner_id        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id)
);


CREATE TABLE IF NOT EXISTS deployments (
    id              TEXT PRIMARY KEY,
    target_id       TEXT NOT NULL,
    object_id       TEXT,
    scope_id        TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    state           TEXT NOT NULL DEFAULT 'pending',
    deployed_at     TEXT,
    deployed_by     TEXT NOT NULL,
    rollback_of     TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (target_id) REFERENCES deployment_targets(id),
    FOREIGN KEY (object_id) REFERENCES scoped_objects(id),
    FOREIGN KEY (scope_id) REFERENCES scopes(id),
    FOREIGN KEY (rollback_of) REFERENCES deployments(id)
);

CREATE INDEX IF NOT EXISTS idx_deploy_target ON deployments(target_id);
CREATE INDEX IF NOT EXISTS idx_deploy_state ON deployments(state);


CREATE TABLE IF NOT EXISTS deployment_gates (
    id              TEXT PRIMARY KEY,
    deployment_id   TEXT NOT NULL,
    gate_type       TEXT NOT NULL,
    passed          INTEGER NOT NULL DEFAULT 0,
    checked_at      TEXT NOT NULL,
    details_json    TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (deployment_id) REFERENCES deployments(id)
);

CREATE INDEX IF NOT EXISTS idx_gates_deployment ON deployment_gates(deployment_id);


-- =====================================================================
-- SECRETS (encrypted vault)
-- =====================================================================

CREATE TABLE IF NOT EXISTS secrets (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    owner_id        TEXT NOT NULL,
    object_id       TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1,
    classification  TEXT NOT NULL DEFAULT 'standard',
    created_at      TEXT NOT NULL,
    expires_at      TEXT,
    last_rotated_at TEXT,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id),
    FOREIGN KEY (object_id) REFERENCES scoped_objects(id)
);

CREATE INDEX IF NOT EXISTS idx_secrets_owner ON secrets(owner_id);
CREATE INDEX IF NOT EXISTS idx_secrets_classification ON secrets(classification);


CREATE TABLE IF NOT EXISTS secret_versions (
    id              TEXT PRIMARY KEY,
    secret_id       TEXT NOT NULL,
    version         INTEGER NOT NULL,
    encrypted_value TEXT NOT NULL,
    encryption_algo TEXT NOT NULL DEFAULT 'fernet',
    key_id          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (secret_id) REFERENCES secrets(id),
    UNIQUE(secret_id, version)
);

CREATE INDEX IF NOT EXISTS idx_secret_versions ON secret_versions(secret_id);


CREATE TABLE IF NOT EXISTS secret_refs (
    id              TEXT PRIMARY KEY,
    secret_id       TEXT NOT NULL,
    ref_token       TEXT NOT NULL UNIQUE,
    granted_to      TEXT NOT NULL,
    scope_id        TEXT,
    environment_id  TEXT,
    granted_at      TEXT NOT NULL,
    granted_by      TEXT NOT NULL,
    expires_at      TEXT,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (secret_id) REFERENCES secrets(id),
    FOREIGN KEY (granted_to) REFERENCES principals(id)
);

CREATE INDEX IF NOT EXISTS idx_secret_refs_token ON secret_refs(ref_token);
CREATE INDEX IF NOT EXISTS idx_secret_refs_principal ON secret_refs(granted_to);
CREATE INDEX IF NOT EXISTS idx_secret_refs_secret ON secret_refs(secret_id);


CREATE TABLE IF NOT EXISTS secret_access_log (
    id              TEXT PRIMARY KEY,
    secret_id       TEXT NOT NULL,
    ref_id          TEXT,
    accessor_id     TEXT NOT NULL,
    access_type     TEXT NOT NULL,
    accessed_at     TEXT NOT NULL,
    environment_id  TEXT,
    scope_id        TEXT,
    result          TEXT NOT NULL DEFAULT 'success',
    FOREIGN KEY (secret_id) REFERENCES secrets(id),
    FOREIGN KEY (accessor_id) REFERENCES principals(id)
);

CREATE INDEX IF NOT EXISTS idx_secret_access_secret ON secret_access_log(secret_id);
CREATE INDEX IF NOT EXISTS idx_secret_access_time ON secret_access_log(accessed_at);


CREATE TABLE IF NOT EXISTS secret_policies (
    id              TEXT PRIMARY KEY,
    secret_id       TEXT,
    classification  TEXT,
    max_age_seconds INTEGER,
    auto_rotate     INTEGER NOT NULL DEFAULT 0,
    allowed_scopes  TEXT NOT NULL DEFAULT '[]',
    allowed_envs    TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    FOREIGN KEY (secret_id) REFERENCES secrets(id)
);


-- =====================================================================
-- INTEGRATIONS & PLUGINS
-- =====================================================================

CREATE TABLE IF NOT EXISTS integrations (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    integration_type TEXT NOT NULL,
    owner_id        TEXT NOT NULL,
    scope_id        TEXT,
    config_json     TEXT NOT NULL DEFAULT '{}',
    credentials_ref TEXT,
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (owner_id) REFERENCES principals(id),
    FOREIGN KEY (scope_id) REFERENCES scopes(id)
);

CREATE INDEX IF NOT EXISTS idx_integrations_owner ON integrations(owner_id);
CREATE INDEX IF NOT EXISTS idx_integrations_type ON integrations(integration_type);


CREATE TABLE IF NOT EXISTS plugins (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT NOT NULL DEFAULT '',
    version         TEXT NOT NULL DEFAULT '0.1.0',
    owner_id        TEXT NOT NULL,
    scope_id        TEXT,
    manifest_json   TEXT NOT NULL DEFAULT '{}',
    state           TEXT NOT NULL DEFAULT 'installed',
    installed_at    TEXT NOT NULL,
    activated_at    TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (owner_id) REFERENCES principals(id),
    FOREIGN KEY (scope_id) REFERENCES scopes(id)
);

CREATE INDEX IF NOT EXISTS idx_plugins_state ON plugins(state);


CREATE TABLE IF NOT EXISTS plugin_hooks (
    id              TEXT PRIMARY KEY,
    plugin_id       TEXT NOT NULL,
    hook_point      TEXT NOT NULL,
    handler_ref     TEXT NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 0,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (plugin_id) REFERENCES plugins(id)
);

CREATE INDEX IF NOT EXISTS idx_plugin_hooks_point ON plugin_hooks(hook_point);
CREATE INDEX IF NOT EXISTS idx_plugin_hooks_plugin ON plugin_hooks(plugin_id);


CREATE TABLE IF NOT EXISTS plugin_permissions (
    id              TEXT PRIMARY KEY,
    plugin_id       TEXT NOT NULL,
    permission_type TEXT NOT NULL,
    target_ref      TEXT NOT NULL,
    granted_at      TEXT NOT NULL,
    granted_by      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (plugin_id) REFERENCES plugins(id)
);

CREATE INDEX IF NOT EXISTS idx_plugin_perms ON plugin_permissions(plugin_id);


-- =====================================================================
-- PLATFORM CONNECTOR & MARKETPLACE
-- =====================================================================

CREATE TABLE IF NOT EXISTS connectors (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    local_org_id    TEXT NOT NULL,
    remote_org_id   TEXT NOT NULL,
    remote_endpoint TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'proposed',
    direction       TEXT NOT NULL DEFAULT 'bidirectional',
    local_scope_id  TEXT,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    approved_at     TEXT,
    approved_by     TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (local_org_id) REFERENCES principals(id),
    FOREIGN KEY (local_scope_id) REFERENCES scopes(id)
);

CREATE INDEX IF NOT EXISTS idx_connectors_state ON connectors(state);
CREATE INDEX IF NOT EXISTS idx_connectors_org ON connectors(local_org_id);


CREATE TABLE IF NOT EXISTS connector_policies (
    id              TEXT PRIMARY KEY,
    connector_id    TEXT NOT NULL,
    policy_type     TEXT NOT NULL,
    config_json     TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    FOREIGN KEY (connector_id) REFERENCES connectors(id)
);

CREATE INDEX IF NOT EXISTS idx_connector_policies ON connector_policies(connector_id);


CREATE TABLE IF NOT EXISTS connector_traffic (
    id              TEXT PRIMARY KEY,
    connector_id    TEXT NOT NULL,
    direction       TEXT NOT NULL,
    object_type     TEXT NOT NULL,
    object_id       TEXT,
    action          TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'success',
    size_bytes      INTEGER,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (connector_id) REFERENCES connectors(id)
);

CREATE INDEX IF NOT EXISTS idx_connector_traffic ON connector_traffic(connector_id, timestamp);


CREATE TABLE IF NOT EXISTS marketplace_listings (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    publisher_id    TEXT NOT NULL,
    listing_type    TEXT NOT NULL,
    version         TEXT NOT NULL DEFAULT '1.0.0',
    config_template TEXT NOT NULL DEFAULT '{}',
    visibility      TEXT NOT NULL DEFAULT 'public',
    published_at    TEXT NOT NULL,
    updated_at      TEXT,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    download_count  INTEGER NOT NULL DEFAULT 0,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (publisher_id) REFERENCES principals(id)
);

CREATE INDEX IF NOT EXISTS idx_marketplace_type ON marketplace_listings(listing_type);
CREATE INDEX IF NOT EXISTS idx_marketplace_visibility ON marketplace_listings(visibility);
CREATE INDEX IF NOT EXISTS idx_marketplace_publisher ON marketplace_listings(publisher_id);


CREATE TABLE IF NOT EXISTS marketplace_reviews (
    id              TEXT PRIMARY KEY,
    listing_id      TEXT NOT NULL,
    reviewer_id     TEXT NOT NULL,
    rating          INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
    review_text     TEXT NOT NULL DEFAULT '',
    reviewed_at     TEXT NOT NULL,
    FOREIGN KEY (listing_id) REFERENCES marketplace_listings(id),
    FOREIGN KEY (reviewer_id) REFERENCES principals(id),
    UNIQUE(listing_id, reviewer_id)
);

CREATE INDEX IF NOT EXISTS idx_reviews_listing ON marketplace_reviews(listing_id);


CREATE TABLE IF NOT EXISTS marketplace_installs (
    id              TEXT PRIMARY KEY,
    listing_id      TEXT NOT NULL,
    installer_id    TEXT NOT NULL,
    installed_at    TEXT NOT NULL,
    version         TEXT NOT NULL,
    config_json     TEXT NOT NULL DEFAULT '{}',
    result_ref      TEXT,
    result_type     TEXT,
    FOREIGN KEY (listing_id) REFERENCES marketplace_listings(id),
    FOREIGN KEY (installer_id) REFERENCES principals(id)
);

CREATE INDEX IF NOT EXISTS idx_installs_listing ON marketplace_installs(listing_id);


-- =====================================================================
-- CONTRACTS & SCHEMA VALIDATION
-- =====================================================================

CREATE TABLE IF NOT EXISTS contracts (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    object_type     TEXT NOT NULL,
    owner_id        TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (owner_id) REFERENCES principals(id)
);

CREATE INDEX IF NOT EXISTS idx_contracts_object_type ON contracts(object_type);
CREATE INDEX IF NOT EXISTS idx_contracts_owner ON contracts(owner_id);
CREATE INDEX IF NOT EXISTS idx_contracts_lifecycle ON contracts(lifecycle);

CREATE TABLE IF NOT EXISTS contract_versions (
    id              TEXT PRIMARY KEY,
    contract_id     TEXT NOT NULL,
    version         INTEGER NOT NULL,
    fields_json     TEXT NOT NULL DEFAULT '[]',
    constraints_json TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    change_reason   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (contract_id) REFERENCES contracts(id),
    UNIQUE(contract_id, version)
);

CREATE INDEX IF NOT EXISTS idx_contract_versions ON contract_versions(contract_id);


-- =====================================================================
-- BLOBS & MEDIA STORAGE
-- =====================================================================

CREATE TABLE IF NOT EXISTS blobs (
    id              TEXT PRIMARY KEY,
    filename        TEXT NOT NULL,
    content_type    TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    content_hash    TEXT NOT NULL,
    owner_id        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    storage_path    TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    object_id       TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (owner_id) REFERENCES principals(id),
    FOREIGN KEY (object_id) REFERENCES scoped_objects(id)
);

CREATE INDEX IF NOT EXISTS idx_blobs_owner ON blobs(owner_id);
CREATE INDEX IF NOT EXISTS idx_blobs_object ON blobs(object_id);
CREATE INDEX IF NOT EXISTS idx_blobs_content_type ON blobs(content_type);
CREATE INDEX IF NOT EXISTS idx_blobs_lifecycle ON blobs(lifecycle);

CREATE TABLE IF NOT EXISTS blob_versions (
    id              TEXT PRIMARY KEY,
    blob_id         TEXT NOT NULL,
    version         INTEGER NOT NULL,
    content_hash    TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    storage_path    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    change_reason   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (blob_id) REFERENCES blobs(id),
    UNIQUE(blob_id, version)
);

CREATE INDEX IF NOT EXISTS idx_blob_versions ON blob_versions(blob_id);


-- =====================================================================
-- SCOPE SETTINGS (configuration hierarchy)
-- =====================================================================

CREATE TABLE IF NOT EXISTS scope_settings (
    id              TEXT PRIMARY KEY,
    scope_id        TEXT NOT NULL,
    key             TEXT NOT NULL,
    value_json      TEXT NOT NULL DEFAULT 'null',
    description     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    updated_at      TEXT,
    updated_by      TEXT,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (scope_id) REFERENCES scopes(id),
    UNIQUE(scope_id, key)
);

CREATE INDEX IF NOT EXISTS idx_scope_settings_scope ON scope_settings(scope_id);
CREATE INDEX IF NOT EXISTS idx_scope_settings_key ON scope_settings(key);


-- =====================================================================
-- SEARCH INDEX (Postgres tsvector full-text search)
-- =====================================================================

CREATE TABLE IF NOT EXISTS search_index (
    id              TEXT PRIMARY KEY,
    object_id       TEXT NOT NULL,
    object_type     TEXT NOT NULL,
    owner_id        TEXT NOT NULL,
    field_name      TEXT NOT NULL,
    content         TEXT NOT NULL,
    scope_id        TEXT,
    indexed_at      TEXT NOT NULL,
    search_vector   tsvector,
    FOREIGN KEY (object_id) REFERENCES scoped_objects(id)
);

CREATE INDEX IF NOT EXISTS idx_search_object ON search_index(object_id);
CREATE INDEX IF NOT EXISTS idx_search_owner ON search_index(owner_id);
CREATE INDEX IF NOT EXISTS idx_search_type ON search_index(object_type);
CREATE INDEX IF NOT EXISTS idx_search_scope ON search_index(scope_id);
CREATE INDEX IF NOT EXISTS idx_search_fts ON search_index USING gin(search_vector);


-- =====================================================================
-- TEMPLATES (general-purpose blueprints)
-- =====================================================================

CREATE TABLE IF NOT EXISTS templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    template_type   TEXT NOT NULL,
    owner_id        TEXT NOT NULL,
    schema_json     TEXT NOT NULL DEFAULT '{}',
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    scope_id        TEXT,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id),
    FOREIGN KEY (scope_id) REFERENCES scopes(id)
);

CREATE INDEX IF NOT EXISTS idx_templates_owner ON templates(owner_id);
CREATE INDEX IF NOT EXISTS idx_templates_type ON templates(template_type);
CREATE INDEX IF NOT EXISTS idx_templates_scope ON templates(scope_id);
CREATE INDEX IF NOT EXISTS idx_templates_lifecycle ON templates(lifecycle);

CREATE TABLE IF NOT EXISTS template_versions (
    id              TEXT PRIMARY KEY,
    template_id     TEXT NOT NULL,
    version         INTEGER NOT NULL,
    schema_json     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    change_reason   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (template_id) REFERENCES templates(id),
    UNIQUE(template_id, version)
);

CREATE INDEX IF NOT EXISTS idx_template_versions ON template_versions(template_id);


-- =====================================================================
-- STORAGE TIERING & ARCHIVAL
-- =====================================================================

CREATE TABLE IF NOT EXISTS tier_assignments (
    id              TEXT PRIMARY KEY,
    object_id       TEXT NOT NULL,
    version         INTEGER NOT NULL,
    tier            TEXT NOT NULL DEFAULT 'HOT',
    assigned_at     TEXT NOT NULL,
    assigned_by     TEXT NOT NULL,
    previous_tier   TEXT,
    FOREIGN KEY (object_id) REFERENCES scoped_objects(id),
    UNIQUE(object_id, version)
);

CREATE INDEX IF NOT EXISTS idx_tier_object ON tier_assignments(object_id);
CREATE INDEX IF NOT EXISTS idx_tier_tier ON tier_assignments(tier);
CREATE INDEX IF NOT EXISTS idx_tier_assigned ON tier_assignments(assigned_at);

CREATE TABLE IF NOT EXISTS retention_policies (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    source_tier     TEXT NOT NULL,
    target_tier     TEXT NOT NULL,
    condition_type  TEXT NOT NULL,
    condition_value TEXT NOT NULL,
    object_type     TEXT,
    scope_id        TEXT,
    owner_id        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id),
    FOREIGN KEY (scope_id) REFERENCES scopes(id)
);

CREATE INDEX IF NOT EXISTS idx_retention_owner ON retention_policies(owner_id);
CREATE INDEX IF NOT EXISTS idx_retention_lifecycle ON retention_policies(lifecycle);

CREATE TABLE IF NOT EXISTS glacial_archives (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    object_ids_json TEXT NOT NULL DEFAULT '[]',
    owner_id        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    sealed          INTEGER NOT NULL DEFAULT 0,
    sealed_at       TEXT,
    content_hash    TEXT NOT NULL,
    compressed_data BYTEA NOT NULL,
    compressed_size INTEGER NOT NULL,
    original_size   INTEGER NOT NULL,
    entry_count     INTEGER NOT NULL DEFAULT 0,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id)
);

CREATE INDEX IF NOT EXISTS idx_archive_owner ON glacial_archives(owner_id);
CREATE INDEX IF NOT EXISTS idx_archive_sealed ON glacial_archives(sealed);

-- =====================================================================
-- EVENTS & WEBHOOKS
-- =====================================================================

CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    actor_id        TEXT NOT NULL,
    target_type     TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    scope_id        TEXT,
    data_json       TEXT NOT NULL DEFAULT '{}',
    source_trace_id TEXT,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_actor ON events(actor_id);
CREATE INDEX IF NOT EXISTS idx_events_scope ON events(scope_id);
CREATE INDEX IF NOT EXISTS idx_events_target ON events(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);

CREATE TABLE IF NOT EXISTS event_subscriptions (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    owner_id            TEXT NOT NULL,
    event_types_json    TEXT NOT NULL DEFAULT '[]',
    target_types_json   TEXT NOT NULL DEFAULT '[]',
    scope_id            TEXT,
    webhook_endpoint_id TEXT,
    created_at          TEXT NOT NULL,
    lifecycle           TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id)
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_owner ON event_subscriptions(owner_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_lifecycle ON event_subscriptions(lifecycle);

CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    owner_id        TEXT NOT NULL,
    url             TEXT NOT NULL,
    config_json     TEXT NOT NULL DEFAULT '{}',
    scope_id        TEXT,
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id)
);

CREATE INDEX IF NOT EXISTS idx_webhooks_owner ON webhook_endpoints(owner_id);
CREATE INDEX IF NOT EXISTS idx_webhooks_lifecycle ON webhook_endpoints(lifecycle);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id                  TEXT PRIMARY KEY,
    event_id            TEXT NOT NULL REFERENCES events(id),
    webhook_endpoint_id TEXT NOT NULL REFERENCES webhook_endpoints(id),
    subscription_id     TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    attempted_at        TEXT NOT NULL,
    attempt_number      INTEGER NOT NULL DEFAULT 0,
    response_status     INTEGER,
    response_body       TEXT,
    error_message       TEXT
);

CREATE INDEX IF NOT EXISTS idx_deliveries_event ON webhook_deliveries(event_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_status ON webhook_deliveries(status);
CREATE INDEX IF NOT EXISTS idx_deliveries_endpoint ON webhook_deliveries(webhook_endpoint_id);

-- =====================================================================
-- NOTIFICATIONS
-- =====================================================================

CREATE TABLE IF NOT EXISTS notifications (
    id              TEXT PRIMARY KEY,
    recipient_id    TEXT NOT NULL,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    channel         TEXT NOT NULL DEFAULT 'in_app',
    status          TEXT NOT NULL DEFAULT 'unread',
    created_at      TEXT NOT NULL,
    source_event_id TEXT,
    source_rule_id  TEXT,
    scope_id        TEXT,
    data_json       TEXT NOT NULL DEFAULT '{}',
    read_at         TEXT,
    dismissed_at    TEXT,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications(recipient_id);
CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status);
CREATE INDEX IF NOT EXISTS idx_notifications_channel ON notifications(channel);
CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at);

CREATE TABLE IF NOT EXISTS notification_rules (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    owner_id            TEXT NOT NULL,
    event_types_json    TEXT NOT NULL DEFAULT '[]',
    target_types_json   TEXT NOT NULL DEFAULT '[]',
    scope_id            TEXT,
    recipient_ids_json  TEXT NOT NULL DEFAULT '[]',
    channel             TEXT NOT NULL DEFAULT 'in_app',
    title_template      TEXT NOT NULL DEFAULT '{event_type}',
    body_template       TEXT NOT NULL DEFAULT '{target_type} {target_id}',
    created_at          TEXT NOT NULL,
    lifecycle           TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id)
);

CREATE INDEX IF NOT EXISTS idx_notif_rules_owner ON notification_rules(owner_id);
CREATE INDEX IF NOT EXISTS idx_notif_rules_lifecycle ON notification_rules(lifecycle);

CREATE TABLE IF NOT EXISTS notification_preferences (
    id              TEXT PRIMARY KEY,
    principal_id    TEXT NOT NULL,
    channel         TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    UNIQUE(principal_id, channel)
);

CREATE INDEX IF NOT EXISTS idx_notif_prefs_principal ON notification_preferences(principal_id);

-- =====================================================================
-- SCHEDULING & JOBS
-- =====================================================================

CREATE TABLE IF NOT EXISTS recurring_schedules (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    owner_id            TEXT NOT NULL,
    cron_expression     TEXT,
    interval_seconds    INTEGER,
    created_at          TEXT NOT NULL,
    lifecycle           TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id)
);

CREATE INDEX IF NOT EXISTS idx_schedules_owner ON recurring_schedules(owner_id);
CREATE INDEX IF NOT EXISTS idx_schedules_lifecycle ON recurring_schedules(lifecycle);

CREATE TABLE IF NOT EXISTS scheduled_actions (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    owner_id            TEXT NOT NULL,
    action_type         TEXT NOT NULL,
    action_config_json  TEXT NOT NULL DEFAULT '{}',
    next_run_at         TEXT NOT NULL,
    schedule_id         TEXT REFERENCES recurring_schedules(id),
    scope_id            TEXT,
    created_at          TEXT NOT NULL,
    lifecycle           TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id)
);

CREATE INDEX IF NOT EXISTS idx_actions_owner ON scheduled_actions(owner_id);
CREATE INDEX IF NOT EXISTS idx_actions_next_run ON scheduled_actions(next_run_at);
CREATE INDEX IF NOT EXISTS idx_actions_lifecycle ON scheduled_actions(lifecycle);

CREATE TABLE IF NOT EXISTS jobs (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    action_type         TEXT NOT NULL,
    action_config_json  TEXT NOT NULL DEFAULT '{}',
    owner_id            TEXT NOT NULL,
    state               TEXT NOT NULL DEFAULT 'queued',
    created_at          TEXT NOT NULL,
    started_at          TEXT,
    completed_at        TEXT,
    result_json         TEXT NOT NULL DEFAULT '{}',
    error_message       TEXT,
    scheduled_action_id TEXT REFERENCES scheduled_actions(id),
    scope_id            TEXT,
    lifecycle           TEXT NOT NULL DEFAULT 'ACTIVE',
    FOREIGN KEY (owner_id) REFERENCES principals(id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner_id);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);

-- =====================================================================
-- SYNC STATE (management plane watermark)
-- =====================================================================

CREATE TABLE IF NOT EXISTS _sync_state (
    id              TEXT PRIMARY KEY DEFAULT 'singleton',
    last_sequence   INTEGER NOT NULL DEFAULT 0,
    last_hash       TEXT NOT NULL DEFAULT '',
    last_synced_at  TEXT,
    last_batch_id   TEXT,
    status          TEXT NOT NULL DEFAULT 'idle',
    error_message   TEXT,
    error_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL DEFAULT ''
);
"""
