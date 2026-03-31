"""Layer/table manifest — single source of truth for compliance coverage.

Declares every layer, its tables, applicable invariants, and capabilities.
Consumed by health.py, auditor.py, and introspection.py instead of hardcoded lists.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LayerSpec:
    """Specification for a single Scoped layer."""

    number: int
    name: str
    module: str
    tables: tuple[str, ...]
    invariants: tuple[int, ...]  # which of the 10 invariants apply
    has_registry: bool  # does this layer auto-register entities?
    has_audit: bool  # does this layer write audit trails?


@dataclass(frozen=True, slots=True)
class ExtensionSpec:
    """Specification for a Scoped extension."""

    code: str  # e.g. "A1"
    name: str
    tables: tuple[str, ...]
    invariants: tuple[int, ...]


# ---------------------------------------------------------------------------
# Layer Specifications (L1–L16)
# ---------------------------------------------------------------------------

LAYER_SPECS: tuple[LayerSpec, ...] = (
    LayerSpec(
        number=1,
        name="Registry",
        module="scoped.registry",
        tables=("registry_entries",),
        invariants=(1,),
        has_registry=False,  # registry IS the registry
        has_audit=False,
    ),
    LayerSpec(
        number=2,
        name="Identity",
        module="scoped.identity",
        tables=("principals", "principal_relationships"),
        invariants=(1, 2, 4, 8),
        has_registry=True,
        has_audit=True,
    ),
    LayerSpec(
        number=3,
        name="Objects",
        module="scoped.objects",
        tables=("scoped_objects", "object_versions", "tombstones"),
        invariants=(1, 3, 4, 5, 8, 9),
        has_registry=True,
        has_audit=True,
    ),
    LayerSpec(
        number=4,
        name="Tenancy",
        module="scoped.tenancy",
        tables=("scopes", "scope_memberships", "scope_projections"),
        invariants=(1, 3, 4, 7, 8),
        has_registry=True,
        has_audit=True,
    ),
    LayerSpec(
        number=5,
        name="Rules",
        module="scoped.rules",
        tables=("rules", "rule_versions", "rule_bindings"),
        invariants=(1, 4, 6, 8),
        has_registry=True,
        has_audit=True,
    ),
    LayerSpec(
        number=6,
        name="Audit",
        module="scoped.audit",
        tables=("audit_trail",),
        invariants=(4,),
        has_registry=False,
        has_audit=False,  # audit IS the audit layer
    ),
    LayerSpec(
        number=7,
        name="Temporal",
        module="scoped.temporal",
        tables=(),  # uses audit_trail + object_versions, no own tables
        invariants=(9,),
        has_registry=False,
        has_audit=True,
    ),
    LayerSpec(
        number=8,
        name="Environments",
        module="scoped.environments",
        tables=(
            "environments",
            "environment_templates",
            "environment_snapshots",
            "environment_objects",
        ),
        invariants=(1, 4, 5, 8),
        has_registry=True,
        has_audit=True,
    ),
    LayerSpec(
        number=9,
        name="Flow",
        module="scoped.flow",
        tables=(
            "stages",
            "pipelines",
            "stage_transitions",
            "flow_channels",
            "promotions",
        ),
        invariants=(1, 4, 8),
        has_registry=True,
        has_audit=True,
    ),
    LayerSpec(
        number=10,
        name="Deployments",
        module="scoped.deployments",
        tables=("deployment_targets", "deployments", "deployment_gates"),
        invariants=(1, 4, 8),
        has_registry=True,
        has_audit=True,
    ),
    LayerSpec(
        number=11,
        name="Secrets",
        module="scoped.secrets",
        tables=(
            "secrets",
            "secret_versions",
            "secret_refs",
            "secret_access_log",
            "secret_policies",
        ),
        invariants=(1, 4, 5, 8, 10),
        has_registry=True,  # via ScopedObject backing
        has_audit=True,
    ),
    LayerSpec(
        number=12,
        name="Integrations",
        module="scoped.integrations",
        tables=("integrations", "plugins", "plugin_hooks", "plugin_permissions"),
        invariants=(1, 4, 5),
        has_registry=True,
        has_audit=True,
    ),
    LayerSpec(
        number=13,
        name="Connector",
        module="scoped.connector",
        tables=(
            "connectors",
            "connector_policies",
            "connector_traffic",
            "marketplace_listings",
            "marketplace_reviews",
            "marketplace_installs",
        ),
        invariants=(1, 4, 5),
        has_registry=True,
        has_audit=True,
    ),
    LayerSpec(
        number=14,
        name="Events",
        module="scoped.events",
        tables=(
            "events",
            "event_subscriptions",
            "webhook_endpoints",
            "webhook_deliveries",
        ),
        invariants=(1, 4),
        has_registry=True,  # subscriptions + webhooks, not individual events
        has_audit=True,
    ),
    LayerSpec(
        number=15,
        name="Notifications",
        module="scoped.notifications",
        tables=(
            "notifications",
            "notification_rules",
            "notification_preferences",
        ),
        invariants=(1, 4),
        has_registry=True,
        has_audit=True,
    ),
    LayerSpec(
        number=16,
        name="Scheduling",
        module="scoped.scheduling",
        tables=("recurring_schedules", "scheduled_actions", "jobs"),
        invariants=(1, 4),
        has_registry=True,
        has_audit=True,
    ),
)


# ---------------------------------------------------------------------------
# Extension Specifications (A1–A9)
# ---------------------------------------------------------------------------

EXTENSION_SPECS: tuple[ExtensionSpec, ...] = (
    ExtensionSpec(
        code="A1",
        name="Migrations",
        tables=(),  # scoped_migrations created by MigrationRunner, not SCHEMA_SQL
        invariants=(),
    ),
    ExtensionSpec(
        code="A2",
        name="Contracts",
        tables=("contracts", "contract_versions"),
        invariants=(1, 8),
    ),
    ExtensionSpec(
        code="A3",
        name="Rule Extensions",
        tables=(),  # uses existing rules/rule_bindings tables
        invariants=(6,),
    ),
    ExtensionSpec(
        code="A4",
        name="Blobs",
        tables=("blobs", "blob_versions"),
        invariants=(1, 5, 8),
    ),
    ExtensionSpec(
        code="A5",
        name="Config Hierarchy",
        tables=("scope_settings",),
        invariants=(8,),
    ),
    ExtensionSpec(
        code="A6",
        name="Search",
        tables=("search_index", "search_index_fts"),
        invariants=(),
    ),
    ExtensionSpec(
        code="A7",
        name="Templates",
        tables=("templates", "template_versions"),
        invariants=(1, 8),
    ),
    ExtensionSpec(
        code="A8",
        name="Tiering",
        tables=("tier_assignments", "retention_policies", "glacial_archives"),
        invariants=(5,),
    ),
    ExtensionSpec(
        code="A9",
        name="Import/Export",
        tables=(),  # operates on existing objects tables
        invariants=(1, 4),
    ),
)


# ---------------------------------------------------------------------------
# Convenience accessors
# ---------------------------------------------------------------------------

def get_all_tables(*, include_extensions: bool = True) -> tuple[str, ...]:
    """Return all table names from layers and optionally extensions."""
    tables: list[str] = []
    for spec in LAYER_SPECS:
        tables.extend(spec.tables)
    if include_extensions:
        for ext in EXTENSION_SPECS:
            tables.extend(ext.tables)
    return tuple(tables)


def get_layer(number: int) -> LayerSpec:
    """Return the LayerSpec for the given layer number."""
    for spec in LAYER_SPECS:
        if spec.number == number:
            return spec
    raise ValueError(f"No layer with number {number}")


def get_extension(code: str) -> ExtensionSpec:
    """Return the ExtensionSpec for the given extension code."""
    for ext in EXTENSION_SPECS:
        if ext.code == code:
            return ext
    raise ValueError(f"No extension with code {code}")


def get_layers_for_invariant(invariant: int) -> tuple[LayerSpec, ...]:
    """Return all layers that must satisfy the given invariant."""
    return tuple(spec for spec in LAYER_SPECS if invariant in spec.invariants)


def get_registry_layers() -> tuple[LayerSpec, ...]:
    """Return all layers that auto-register entities."""
    return tuple(spec for spec in LAYER_SPECS if spec.has_registry)


def get_audit_layers() -> tuple[LayerSpec, ...]:
    """Return all layers that write audit trails."""
    return tuple(spec for spec in LAYER_SPECS if spec.has_audit)
