"""SQLAlchemy Core table definitions for the pyscoped schema.

These definitions are for **query building only** (SELECT, INSERT, UPDATE,
DELETE via ``sqlalchemy.select``, ``sqlalchemy.insert``, etc.).  They are
*not* used for DDL — all schema creation and evolution is handled by the
migration files in ``scoped.storage.migrations.versions``.
"""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()

# =====================================================================
# Migration runner bookkeeping
# =====================================================================

scoped_migrations = sa.Table(
    "scoped_migrations",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("version", sa.Integer, nullable=False, unique=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("applied_at", sa.Text, nullable=False),
    sa.Column("checksum", sa.Text, nullable=False, server_default=""),
)

# =====================================================================
# m0001 — Initial schema (Layers 1-13)
# =====================================================================

# -- Registry (Layer 1) -----------------------------------------------

registry_entries = sa.Table(
    "registry_entries",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("urn", sa.Text, nullable=False, unique=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("namespace", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.Column("registered_at", sa.Text, nullable=False),
    sa.Column("registered_by", sa.Text, nullable=False, server_default="system"),
    sa.Column("entry_version", sa.Integer, nullable=False, server_default="1"),
    sa.Column("previous_entry_id", sa.Text),
    sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column("tags_json", sa.Text, nullable=False, server_default="[]"),
)

# -- Identity (Layer 2) -----------------------------------------------

principals = sa.Table(
    "principals",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("display_name", sa.Text, nullable=False, server_default=""),
    sa.Column(
        "registry_entry_id",
        sa.Text,
        sa.ForeignKey("registry_entries.id"),
        nullable=False,
    ),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False, server_default="system"),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
)

principal_relationships = sa.Table(
    "principal_relationships",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "parent_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column(
        "child_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("relationship", sa.Text, nullable=False, server_default="member_of"),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
    sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
    sa.UniqueConstraint("parent_id", "child_id", "relationship"),
)

# -- Objects (Layer 3) -------------------------------------------------

scoped_objects = sa.Table(
    "scoped_objects",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("object_type", sa.Text, nullable=False),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("registry_entry_id", sa.Text),
    sa.Column("current_version", sa.Integer, nullable=False, server_default="1"),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

object_versions = sa.Table(
    "object_versions",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "object_id", sa.Text, sa.ForeignKey("scoped_objects.id"), nullable=False
    ),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("data_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
    sa.Column("change_reason", sa.Text, nullable=False, server_default=""),
    sa.Column("checksum", sa.Text, nullable=False, server_default=""),
    sa.UniqueConstraint("object_id", "version"),
)

tombstones = sa.Table(
    "tombstones",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "object_id",
        sa.Text,
        sa.ForeignKey("scoped_objects.id"),
        nullable=False,
        unique=True,
    ),
    sa.Column("tombstoned_at", sa.Text, nullable=False),
    sa.Column("tombstoned_by", sa.Text, nullable=False),
    sa.Column("reason", sa.Text, nullable=False, server_default=""),
)

# -- Tenancy (Layer 4) -------------------------------------------------

scopes = sa.Table(
    "scopes",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("parent_scope_id", sa.Text, sa.ForeignKey("scopes.id")),
    sa.Column("registry_entry_id", sa.Text),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
)

scope_memberships = sa.Table(
    "scope_memberships",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "scope_id", sa.Text, sa.ForeignKey("scopes.id"), nullable=False
    ),
    sa.Column(
        "principal_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("role", sa.Text, nullable=False, server_default="viewer"),
    sa.Column("granted_at", sa.Text, nullable=False),
    sa.Column("granted_by", sa.Text, nullable=False),
    sa.Column("expires_at", sa.Text),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.UniqueConstraint("scope_id", "principal_id", "role"),
)

scope_projections = sa.Table(
    "scope_projections",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "scope_id", sa.Text, sa.ForeignKey("scopes.id"), nullable=False
    ),
    sa.Column(
        "object_id", sa.Text, sa.ForeignKey("scoped_objects.id"), nullable=False
    ),
    sa.Column("projected_at", sa.Text, nullable=False),
    sa.Column("projected_by", sa.Text, nullable=False),
    sa.Column("access_level", sa.Text, nullable=False, server_default="read"),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.UniqueConstraint("scope_id", "object_id"),
)

# -- Rules (Layer 5) ---------------------------------------------------

rules = sa.Table(
    "rules",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column("rule_type", sa.Text, nullable=False),
    sa.Column("effect", sa.Text, nullable=False),
    sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
    sa.Column("conditions_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column("registry_entry_id", sa.Text),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.Column("current_version", sa.Integer, nullable=False, server_default="1"),
)

rule_versions = sa.Table(
    "rule_versions",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "rule_id", sa.Text, sa.ForeignKey("rules.id"), nullable=False
    ),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("conditions_json", sa.Text, nullable=False),
    sa.Column("effect", sa.Text, nullable=False),
    sa.Column("priority", sa.Integer, nullable=False),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
    sa.Column("change_reason", sa.Text, nullable=False, server_default=""),
    sa.UniqueConstraint("rule_id", "version"),
)

rule_bindings = sa.Table(
    "rule_bindings",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "rule_id", sa.Text, sa.ForeignKey("rules.id"), nullable=False
    ),
    sa.Column("target_type", sa.Text, nullable=False),
    sa.Column("target_id", sa.Text, nullable=False),
    sa.Column("bound_at", sa.Text, nullable=False),
    sa.Column("bound_by", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.UniqueConstraint("rule_id", "target_type", "target_id"),
)

# -- Audit (Layer 6) ---------------------------------------------------

audit_trail = sa.Table(
    "audit_trail",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("sequence", sa.Integer, nullable=False),
    sa.Column("actor_id", sa.Text, nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("target_type", sa.Text, nullable=False),
    sa.Column("target_id", sa.Text, nullable=False),
    sa.Column("scope_id", sa.Text),
    sa.Column("timestamp", sa.Text, nullable=False),
    sa.Column("before_state", sa.Text),
    sa.Column("after_state", sa.Text),
    sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column(
        "parent_trace_id", sa.Text, sa.ForeignKey("audit_trail.id")
    ),
    sa.Column("hash", sa.Text, nullable=False),
    sa.Column("previous_hash", sa.Text, nullable=False, server_default=""),
)

# -- Environments (Layer 8) --------------------------------------------

environment_templates = sa.Table(
    "environment_templates",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("config_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

environments = sa.Table(
    "environments",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column(
        "template_id", sa.Text, sa.ForeignKey("environment_templates.id")
    ),
    sa.Column("scope_id", sa.Text, sa.ForeignKey("scopes.id")),
    sa.Column("state", sa.Text, nullable=False, server_default="spawning"),
    sa.Column("ephemeral", sa.Boolean, nullable=False, server_default="1"),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("completed_at", sa.Text),
    sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
)

environment_snapshots = sa.Table(
    "environment_snapshots",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "environment_id",
        sa.Text,
        sa.ForeignKey("environments.id"),
        nullable=False,
    ),
    sa.Column("name", sa.Text, nullable=False, server_default=""),
    sa.Column("snapshot_data", sa.Text, nullable=False),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
    sa.Column("checksum", sa.Text, nullable=False, server_default=""),
)

environment_objects = sa.Table(
    "environment_objects",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "environment_id",
        sa.Text,
        sa.ForeignKey("environments.id"),
        nullable=False,
    ),
    sa.Column(
        "object_id",
        sa.Text,
        sa.ForeignKey("scoped_objects.id"),
        nullable=False,
    ),
    sa.Column("origin", sa.Text, nullable=False, server_default="created"),
    sa.Column("added_at", sa.Text, nullable=False),
    sa.UniqueConstraint("environment_id", "object_id"),
)

# -- Stages & Flow (Layer 9) -------------------------------------------

pipelines = sa.Table(
    "pipelines",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

stages = sa.Table(
    "stages",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("pipeline_id", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
    sa.UniqueConstraint("pipeline_id", "name"),
)

stage_transitions = sa.Table(
    "stage_transitions",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "object_id",
        sa.Text,
        sa.ForeignKey("scoped_objects.id"),
        nullable=False,
    ),
    sa.Column("from_stage_id", sa.Text, sa.ForeignKey("stages.id")),
    sa.Column(
        "to_stage_id", sa.Text, sa.ForeignKey("stages.id"), nullable=False
    ),
    sa.Column("transitioned_at", sa.Text, nullable=False),
    sa.Column("transitioned_by", sa.Text, nullable=False),
    sa.Column("reason", sa.Text, nullable=False, server_default=""),
)

flow_channels = sa.Table(
    "flow_channels",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("source_type", sa.Text, nullable=False),
    sa.Column("source_id", sa.Text, nullable=False),
    sa.Column("target_type", sa.Text, nullable=False),
    sa.Column("target_id", sa.Text, nullable=False),
    sa.Column("allowed_types", sa.Text, nullable=False, server_default="[]"),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

promotions = sa.Table(
    "promotions",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "object_id",
        sa.Text,
        sa.ForeignKey("scoped_objects.id"),
        nullable=False,
    ),
    sa.Column(
        "source_env_id",
        sa.Text,
        sa.ForeignKey("environments.id"),
        nullable=False,
    ),
    sa.Column(
        "target_scope_id",
        sa.Text,
        sa.ForeignKey("scopes.id"),
        nullable=False,
    ),
    sa.Column("target_stage_id", sa.Text, sa.ForeignKey("stages.id")),
    sa.Column("promoted_at", sa.Text, nullable=False),
    sa.Column("promoted_by", sa.Text, nullable=False),
)

# -- Deployments (Layer 10) --------------------------------------------

deployment_targets = sa.Table(
    "deployment_targets",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("target_type", sa.Text, nullable=False),
    sa.Column("config_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

deployments = sa.Table(
    "deployments",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "target_id",
        sa.Text,
        sa.ForeignKey("deployment_targets.id"),
        nullable=False,
    ),
    sa.Column("object_id", sa.Text, sa.ForeignKey("scoped_objects.id")),
    sa.Column("scope_id", sa.Text, sa.ForeignKey("scopes.id")),
    sa.Column("version", sa.Integer, nullable=False, server_default="1"),
    sa.Column("state", sa.Text, nullable=False, server_default="pending"),
    sa.Column("deployed_at", sa.Text),
    sa.Column("deployed_by", sa.Text, nullable=False),
    sa.Column("rollback_of", sa.Text, sa.ForeignKey("deployments.id")),
    sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
)

deployment_gates = sa.Table(
    "deployment_gates",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "deployment_id",
        sa.Text,
        sa.ForeignKey("deployments.id"),
        nullable=False,
    ),
    sa.Column("gate_type", sa.Text, nullable=False),
    sa.Column("passed", sa.Boolean, nullable=False, server_default="0"),
    sa.Column("checked_at", sa.Text, nullable=False),
    sa.Column("details_json", sa.Text, nullable=False, server_default="{}"),
)

# -- Secrets (Layer 11) -------------------------------------------------

secrets = sa.Table(
    "secrets",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column(
        "object_id",
        sa.Text,
        sa.ForeignKey("scoped_objects.id"),
        nullable=False,
    ),
    sa.Column("current_version", sa.Integer, nullable=False, server_default="1"),
    sa.Column("classification", sa.Text, nullable=False, server_default="standard"),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("expires_at", sa.Text),
    sa.Column("last_rotated_at", sa.Text),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

secret_versions = sa.Table(
    "secret_versions",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "secret_id", sa.Text, sa.ForeignKey("secrets.id"), nullable=False
    ),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("encrypted_value", sa.Text, nullable=False),
    sa.Column("encryption_algo", sa.Text, nullable=False, server_default="fernet"),
    sa.Column("key_id", sa.Text, nullable=False),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
    sa.Column("reason", sa.Text, nullable=False, server_default=""),
    sa.UniqueConstraint("secret_id", "version"),
)

secret_refs = sa.Table(
    "secret_refs",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "secret_id", sa.Text, sa.ForeignKey("secrets.id"), nullable=False
    ),
    sa.Column("ref_token", sa.Text, nullable=False, unique=True),
    sa.Column(
        "granted_to", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("scope_id", sa.Text),
    sa.Column("environment_id", sa.Text),
    sa.Column("granted_at", sa.Text, nullable=False),
    sa.Column("granted_by", sa.Text, nullable=False),
    sa.Column("expires_at", sa.Text),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

secret_access_log = sa.Table(
    "secret_access_log",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "secret_id", sa.Text, sa.ForeignKey("secrets.id"), nullable=False
    ),
    sa.Column("ref_id", sa.Text),
    sa.Column(
        "accessor_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("access_type", sa.Text, nullable=False),
    sa.Column("accessed_at", sa.Text, nullable=False),
    sa.Column("environment_id", sa.Text),
    sa.Column("scope_id", sa.Text),
    sa.Column("result", sa.Text, nullable=False, server_default="success"),
)

secret_policies = sa.Table(
    "secret_policies",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("secret_id", sa.Text, sa.ForeignKey("secrets.id")),
    sa.Column("classification", sa.Text),
    sa.Column("max_age_seconds", sa.Integer),
    sa.Column("auto_rotate", sa.Boolean, nullable=False, server_default="0"),
    sa.Column("allowed_scopes", sa.Text, nullable=False, server_default="[]"),
    sa.Column("allowed_envs", sa.Text, nullable=False, server_default="[]"),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
)

# -- Integrations & Plugins (Layer 12) ---------------------------------

integrations = sa.Table(
    "integrations",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column("integration_type", sa.Text, nullable=False),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("scope_id", sa.Text, sa.ForeignKey("scopes.id")),
    sa.Column("config_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column("credentials_ref", sa.Text),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
)

plugins = sa.Table(
    "plugins",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False, unique=True),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column("version", sa.Text, nullable=False, server_default="0.1.0"),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("scope_id", sa.Text, sa.ForeignKey("scopes.id")),
    sa.Column("manifest_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column("state", sa.Text, nullable=False, server_default="installed"),
    sa.Column("installed_at", sa.Text, nullable=False),
    sa.Column("activated_at", sa.Text),
    sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
)

plugin_hooks = sa.Table(
    "plugin_hooks",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "plugin_id", sa.Text, sa.ForeignKey("plugins.id"), nullable=False
    ),
    sa.Column("hook_point", sa.Text, nullable=False),
    sa.Column("handler_ref", sa.Text, nullable=False),
    sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

plugin_permissions = sa.Table(
    "plugin_permissions",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "plugin_id", sa.Text, sa.ForeignKey("plugins.id"), nullable=False
    ),
    sa.Column("permission_type", sa.Text, nullable=False),
    sa.Column("target_ref", sa.Text, nullable=False),
    sa.Column("granted_at", sa.Text, nullable=False),
    sa.Column("granted_by", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

# -- Connector & Marketplace (Layer 13) --------------------------------

connectors = sa.Table(
    "connectors",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column(
        "local_org_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("remote_org_id", sa.Text, nullable=False),
    sa.Column("remote_endpoint", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=False, server_default="proposed"),
    sa.Column("direction", sa.Text, nullable=False, server_default="bidirectional"),
    sa.Column("local_scope_id", sa.Text, sa.ForeignKey("scopes.id")),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
    sa.Column("approved_at", sa.Text),
    sa.Column("approved_by", sa.Text),
    sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
)

connector_policies = sa.Table(
    "connector_policies",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "connector_id",
        sa.Text,
        sa.ForeignKey("connectors.id"),
        nullable=False,
    ),
    sa.Column("policy_type", sa.Text, nullable=False),
    sa.Column("config_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
)

connector_traffic = sa.Table(
    "connector_traffic",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "connector_id",
        sa.Text,
        sa.ForeignKey("connectors.id"),
        nullable=False,
    ),
    sa.Column("direction", sa.Text, nullable=False),
    sa.Column("object_type", sa.Text, nullable=False),
    sa.Column("object_id", sa.Text),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("timestamp", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="success"),
    sa.Column("size_bytes", sa.Integer),
    sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
)

marketplace_listings = sa.Table(
    "marketplace_listings",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column(
        "publisher_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("listing_type", sa.Text, nullable=False),
    sa.Column("version", sa.Text, nullable=False, server_default="1.0.0"),
    sa.Column("config_template", sa.Text, nullable=False, server_default="{}"),
    sa.Column("visibility", sa.Text, nullable=False, server_default="public"),
    sa.Column("published_at", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.Column("download_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
)

marketplace_reviews = sa.Table(
    "marketplace_reviews",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "listing_id",
        sa.Text,
        sa.ForeignKey("marketplace_listings.id"),
        nullable=False,
    ),
    sa.Column(
        "reviewer_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column(
        "rating",
        sa.Integer,
        sa.CheckConstraint("rating >= 1 AND rating <= 5"),
        nullable=False,
    ),
    sa.Column("review_text", sa.Text, nullable=False, server_default=""),
    sa.Column("reviewed_at", sa.Text, nullable=False),
    sa.UniqueConstraint("listing_id", "reviewer_id"),
)

marketplace_installs = sa.Table(
    "marketplace_installs",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "listing_id",
        sa.Text,
        sa.ForeignKey("marketplace_listings.id"),
        nullable=False,
    ),
    sa.Column(
        "installer_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("installed_at", sa.Text, nullable=False),
    sa.Column("version", sa.Text, nullable=False),
    sa.Column("config_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column("result_ref", sa.Text),
    sa.Column("result_type", sa.Text),
)

# =====================================================================
# m0002 — Contracts (Extension A2)
# =====================================================================

contracts = sa.Table(
    "contracts",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column("object_type", sa.Text, nullable=False),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("current_version", sa.Integer, nullable=False, server_default="1"),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
)

contract_versions = sa.Table(
    "contract_versions",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "contract_id", sa.Text, sa.ForeignKey("contracts.id"), nullable=False
    ),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("fields_json", sa.Text, nullable=False, server_default="[]"),
    sa.Column("constraints_json", sa.Text, nullable=False, server_default="[]"),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
    sa.Column("change_reason", sa.Text, nullable=False, server_default=""),
    sa.UniqueConstraint("contract_id", "version"),
)

# =====================================================================
# m0003 — Blobs (Extension A4)
# =====================================================================

blobs = sa.Table(
    "blobs",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("filename", sa.Text, nullable=False),
    sa.Column("content_type", sa.Text, nullable=False),
    sa.Column("size_bytes", sa.Integer, nullable=False),
    sa.Column("content_hash", sa.Text, nullable=False),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("storage_path", sa.Text, nullable=False),
    sa.Column("current_version", sa.Integer, nullable=False, server_default="1"),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.Column("object_id", sa.Text, sa.ForeignKey("scoped_objects.id")),
    sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
)

blob_versions = sa.Table(
    "blob_versions",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "blob_id", sa.Text, sa.ForeignKey("blobs.id"), nullable=False
    ),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("content_hash", sa.Text, nullable=False),
    sa.Column("size_bytes", sa.Integer, nullable=False),
    sa.Column("storage_path", sa.Text, nullable=False),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
    sa.Column("change_reason", sa.Text, nullable=False, server_default=""),
    sa.UniqueConstraint("blob_id", "version"),
)

# =====================================================================
# m0004 — Scope Settings (Extension A5)
# =====================================================================

scope_settings = sa.Table(
    "scope_settings",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "scope_id", sa.Text, sa.ForeignKey("scopes.id"), nullable=False
    ),
    sa.Column("key", sa.Text, nullable=False),
    sa.Column("value_json", sa.Text, nullable=False, server_default="null"),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text),
    sa.Column("updated_by", sa.Text),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.UniqueConstraint("scope_id", "key"),
)

# =====================================================================
# m0005 — Search Index (Extension A6)
# =====================================================================

search_index = sa.Table(
    "search_index",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "object_id",
        sa.Text,
        sa.ForeignKey("scoped_objects.id"),
        nullable=False,
    ),
    sa.Column("object_type", sa.Text, nullable=False),
    sa.Column("owner_id", sa.Text, nullable=False),
    sa.Column("field_name", sa.Text, nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("scope_id", sa.Text),
    sa.Column("indexed_at", sa.Text, nullable=False),
)

# =====================================================================
# m0006 — Templates (Extension A7)
# =====================================================================

templates = sa.Table(
    "templates",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column("template_type", sa.Text, nullable=False),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("schema_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column("current_version", sa.Integer, nullable=False, server_default="1"),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("scope_id", sa.Text, sa.ForeignKey("scopes.id")),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

template_versions = sa.Table(
    "template_versions",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "template_id", sa.Text, sa.ForeignKey("templates.id"), nullable=False
    ),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("schema_json", sa.Text, nullable=False),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
    sa.Column("change_reason", sa.Text, nullable=False, server_default=""),
    sa.UniqueConstraint("template_id", "version"),
)

# =====================================================================
# m0007 — Storage Tiering & Archival (Extension A8)
# =====================================================================

tier_assignments = sa.Table(
    "tier_assignments",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "object_id",
        sa.Text,
        sa.ForeignKey("scoped_objects.id"),
        nullable=False,
    ),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("tier", sa.Text, nullable=False, server_default="HOT"),
    sa.Column("assigned_at", sa.Text, nullable=False),
    sa.Column("assigned_by", sa.Text, nullable=False),
    sa.Column("previous_tier", sa.Text),
    sa.UniqueConstraint("object_id", "version"),
)

retention_policies = sa.Table(
    "retention_policies",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column("source_tier", sa.Text, nullable=False),
    sa.Column("target_tier", sa.Text, nullable=False),
    sa.Column("condition_type", sa.Text, nullable=False),
    sa.Column("condition_value", sa.Text, nullable=False),
    sa.Column("object_type", sa.Text),
    sa.Column("scope_id", sa.Text, sa.ForeignKey("scopes.id")),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

glacial_archives = sa.Table(
    "glacial_archives",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=""),
    sa.Column("object_ids_json", sa.Text, nullable=False, server_default="[]"),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("sealed", sa.Boolean, nullable=False, server_default="0"),
    sa.Column("sealed_at", sa.Text),
    sa.Column("content_hash", sa.Text, nullable=False),
    sa.Column("compressed_data", sa.LargeBinary, nullable=False),
    sa.Column("compressed_size", sa.Integer, nullable=False),
    sa.Column("original_size", sa.Integer, nullable=False),
    sa.Column("entry_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

# =====================================================================
# m0008 — Events & Webhooks (Layer 14)
# =====================================================================

events = sa.Table(
    "events",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("actor_id", sa.Text, nullable=False),
    sa.Column("target_type", sa.Text, nullable=False),
    sa.Column("target_id", sa.Text, nullable=False),
    sa.Column("timestamp", sa.Text, nullable=False),
    sa.Column("scope_id", sa.Text),
    sa.Column("data_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column("source_trace_id", sa.Text),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

event_subscriptions = sa.Table(
    "event_subscriptions",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("event_types_json", sa.Text, nullable=False, server_default="[]"),
    sa.Column("target_types_json", sa.Text, nullable=False, server_default="[]"),
    sa.Column("scope_id", sa.Text),
    sa.Column("webhook_endpoint_id", sa.Text),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

webhook_endpoints = sa.Table(
    "webhook_endpoints",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("url", sa.Text, nullable=False),
    sa.Column("config_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column("scope_id", sa.Text),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

webhook_deliveries = sa.Table(
    "webhook_deliveries",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column(
        "event_id", sa.Text, sa.ForeignKey("events.id"), nullable=False
    ),
    sa.Column(
        "webhook_endpoint_id",
        sa.Text,
        sa.ForeignKey("webhook_endpoints.id"),
        nullable=False,
    ),
    sa.Column("subscription_id", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="pending"),
    sa.Column("attempted_at", sa.Text, nullable=False),
    sa.Column("attempt_number", sa.Integer, nullable=False, server_default="0"),
    sa.Column("response_status", sa.Integer),
    sa.Column("response_body", sa.Text),
    sa.Column("error_message", sa.Text),
)

# =====================================================================
# m0009 — Notifications (Layer 15)
# =====================================================================

notifications = sa.Table(
    "notifications",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("recipient_id", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("body", sa.Text, nullable=False, server_default=""),
    sa.Column("channel", sa.Text, nullable=False, server_default="in_app"),
    sa.Column("status", sa.Text, nullable=False, server_default="unread"),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("source_event_id", sa.Text),
    sa.Column("source_rule_id", sa.Text),
    sa.Column("scope_id", sa.Text),
    sa.Column("data_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column("read_at", sa.Text),
    sa.Column("dismissed_at", sa.Text),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

notification_rules = sa.Table(
    "notification_rules",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("event_types_json", sa.Text, nullable=False, server_default="[]"),
    sa.Column("target_types_json", sa.Text, nullable=False, server_default="[]"),
    sa.Column("scope_id", sa.Text),
    sa.Column("recipient_ids_json", sa.Text, nullable=False, server_default="[]"),
    sa.Column("channel", sa.Text, nullable=False, server_default="in_app"),
    sa.Column(
        "title_template",
        sa.Text,
        nullable=False,
        server_default="{event_type}",
    ),
    sa.Column(
        "body_template",
        sa.Text,
        nullable=False,
        server_default="{target_type} {target_id}",
    ),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

notification_preferences = sa.Table(
    "notification_preferences",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("principal_id", sa.Text, nullable=False),
    sa.Column("channel", sa.Text, nullable=False),
    sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
    sa.UniqueConstraint("principal_id", "channel"),
)

# =====================================================================
# m0010 — Scheduling & Jobs (Layer 16)
# =====================================================================

recurring_schedules = sa.Table(
    "recurring_schedules",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("cron_expression", sa.Text),
    sa.Column("interval_seconds", sa.Integer),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

scheduled_actions = sa.Table(
    "scheduled_actions",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("action_type", sa.Text, nullable=False),
    sa.Column("action_config_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column("next_run_at", sa.Text, nullable=False),
    sa.Column(
        "schedule_id", sa.Text, sa.ForeignKey("recurring_schedules.id")
    ),
    sa.Column("scope_id", sa.Text),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

jobs = sa.Table(
    "jobs",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("action_type", sa.Text, nullable=False),
    sa.Column("action_config_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column(
        "owner_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False
    ),
    sa.Column("state", sa.Text, nullable=False, server_default="queued"),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("started_at", sa.Text),
    sa.Column("completed_at", sa.Text),
    sa.Column("result_json", sa.Text, nullable=False, server_default="{}"),
    sa.Column("error_message", sa.Text),
    sa.Column(
        "scheduled_action_id", sa.Text, sa.ForeignKey("scheduled_actions.id")
    ),
    sa.Column("scope_id", sa.Text),
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="ACTIVE"),
)

# =====================================================================
# m0011 — Sync State
# =====================================================================

_sync_state = sa.Table(
    "_sync_state",
    metadata,
    sa.Column("id", sa.Text, primary_key=True, server_default="singleton"),
    sa.Column("last_sequence", sa.Integer, nullable=False, server_default="0"),
    sa.Column("last_hash", sa.Text, nullable=False, server_default=""),
    sa.Column("last_synced_at", sa.Text),
    sa.Column("last_batch_id", sa.Text),
    sa.Column("status", sa.Text, nullable=False, server_default="idle"),
    sa.Column("error_message", sa.Text),
    sa.Column("error_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
)
