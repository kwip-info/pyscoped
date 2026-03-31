"""Migration 0001: Initial schema.

Creates all framework tables as of the original 13-layer implementation.
This migration represents the baseline schema.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scoped.storage.migrations.base import BaseMigration

if TYPE_CHECKING:
    from scoped.storage.interface import StorageBackend


# All table names created in this migration, for rollback.
_TABLES = [
    "marketplace_installs",
    "marketplace_reviews",
    "marketplace_listings",
    "connector_traffic",
    "connector_policies",
    "connectors",
    "plugin_permissions",
    "plugin_hooks",
    "plugins",
    "integrations",
    "secret_policies",
    "secret_access_log",
    "secret_refs",
    "secret_versions",
    "secrets",
    "deployment_gates",
    "deployments",
    "deployment_targets",
    "promotions",
    "flow_channels",
    "stage_transitions",
    "stages",
    "pipelines",
    "environment_objects",
    "environment_snapshots",
    "environment_templates",
    "environments",
    "audit_trail",
    "rule_bindings",
    "rule_versions",
    "rules",
    "scope_projections",
    "scope_memberships",
    "scopes",
    "tombstones",
    "object_versions",
    "scoped_objects",
    "principal_relationships",
    "principals",
    "registry_entries",
]


_UP_SQL = """\
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
-- PRINCIPALS
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
-- SCOPED OBJECTS
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
-- SCOPES
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
-- AUDIT TRAIL
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
-- ENVIRONMENTS
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
-- STAGES & FLOW
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
-- DEPLOYMENTS
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
-- SECRETS
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
"""


class InitialSchema(BaseMigration):
    """Create all framework tables (Layers 1-13)."""

    @property
    def version(self) -> int:
        return 1

    @property
    def name(self) -> str:
        return "initial_schema"

    def up(self, backend: StorageBackend) -> None:
        backend.execute_script(_UP_SQL)

    def down(self, backend: StorageBackend) -> None:
        # Drop all tables in reverse dependency order.
        # We disable foreign keys temporarily for clean drops.
        statements = ["PRAGMA foreign_keys = OFF"]
        statements.extend(f"DROP TABLE IF EXISTS {table}" for table in _TABLES)
        statements.append("PRAGMA foreign_keys = ON")
        backend.execute_script(";\n".join(statements))
