# Scoped API Reference

Complete reference for all 16 layers, the compliance engine, storage backend, and extensions.

**Python:** 3.11+ | **Repository:** https://github.com/kwip-info/pyscoped

---

## Table of Contents

- [Core Types](#core-types)
- [Storage Backend](#storage-backend)
- [Layer 0: Compliance Testing](#layer-0-compliance-testing)
- [Layer 1: Registry](#layer-1-registry)
- [Layer 2: Identity](#layer-2-identity)
- [Layer 3: Objects](#layer-3-objects)
- [Layer 4: Tenancy](#layer-4-tenancy)
- [Layer 5: Rules](#layer-5-rules)
- [Layer 6: Audit](#layer-6-audit)
- [Layer 7: Temporal](#layer-7-temporal)
- [Layer 8: Environments](#layer-8-environments)
- [Layer 9: Flow](#layer-9-flow)
- [Layer 10: Deployments](#layer-10-deployments)
- [Layer 11: Secrets](#layer-11-secrets)
- [Layer 12: Integrations](#layer-12-integrations)
- [Layer 13: Connector](#layer-13-connector)
- [Layer 14: Events](#layer-14-events)
- [Layer 15: Notifications](#layer-15-notifications)
- [Layer 16: Scheduling](#layer-16-scheduling)
- [Extensions](#extensions)
- [Framework Adapters](#framework-adapters)

---

## Core Types

```
from scoped.types import generate_id, now_utc, URN, ActionType, Lifecycle, Metadata
```

### `generate_id() -> str`

Returns a UUID4 hex string. Used as the primary identifier for all framework entities.

### `now_utc() -> datetime`

Returns the current UTC timestamp.

### `URN`

Globally unique name for any registered construct.

```python
@dataclass(frozen=True, slots=True)
class URN:
    kind: str
    namespace: str
    name: str
    version: int = 1
```

- **Format:** `scoped:<kind>:<namespace>:<name>:<version>`
- `URN.parse(raw: str) -> URN` -- Parse a URN string.
- `str(urn)` -- Serialize to string format.

### `Lifecycle` (Enum)

| Value | Description |
|-------|-------------|
| `DRAFT` | Not yet active |
| `ACTIVE` | Normal operation |
| `DEPRECATED` | Superseded; also used internally for frozen scopes |
| `ARCHIVED` | Soft-deleted / dissolved |

### `ActionType` (Enum)

Covers all traceable actions. Key values: `CREATE`, `READ`, `UPDATE`, `DELETE`, `SHARE`, `REVOKE`, `REGISTER`, `UNREGISTER`, `RULE_CHANGE`, `ROLLBACK`, `SCOPE_CREATE`, `SCOPE_MODIFY`, `SCOPE_DISSOLVE`, `MEMBERSHIP_CHANGE`, `OWNERSHIP_TRANSFER`, `LIFECYCLE_CHANGE`, `ACCESS_CHECK`, `PROJECTION`.

Extended values for environments (`ENV_SPAWN`, `ENV_SUSPEND`, `ENV_RESUME`, `ENV_COMPLETE`, `ENV_DISCARD`, `ENV_PROMOTE`, `ENV_SNAPSHOT`), flow (`STAGE_TRANSITION`, `FLOW_PUSH`, `PROMOTION`), deployments (`DEPLOY`, `DEPLOY_ROLLBACK`, `GATE_CHECK`), secrets (`SECRET_CREATE`, `SECRET_READ`, `SECRET_ROTATE`, `SECRET_REVOKE`, `SECRET_REF_GRANT`, `SECRET_REF_RESOLVE`), integrations/plugins, connectors, contracts, blobs, events, notifications, scheduling, and more.

### `Metadata`

```python
@dataclass(slots=True)
class Metadata:
    data: dict[str, Any]
```

- `get(key, default=None) -> Any`
- `set(key, value) -> None`
- `merge(other: dict) -> None`
- `snapshot() -> dict[str, Any]` -- Returns a deep copy.

### Protocols

- `Identifiable` -- Has an `id: str` property.
- `Versioned` -- Has a `version: int` property.
- `Owned` -- Has an `owner_id: str` property.

---

## Storage Backend

```
from scoped.storage.sa_sqlite import SASQLiteBackend
from scoped.storage.interface import StorageBackend, StorageTransaction
```

### `SASQLiteBackend`

```python
class SASQLiteBackend(StorageBackend):
    def __init__(self, path: str = ":memory:") -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `initialize` | `() -> None` | Creates all tables. Must be called once after construction. |
| `execute` | `(sql, params=()) -> Any` | Execute SQL with auto-commit. Returns `lastrowid`. |
| `fetch_one` | `(sql, params=()) -> dict[str, Any] \| None` | Execute query, return first row as dict or `None`. |
| `fetch_all` | `(sql, params=()) -> list[dict[str, Any]]` | Execute query, return all rows as dicts. |
| `transaction` | `() -> SQLiteTransaction` | Start a transaction (context manager). |
| `execute_script` | `(sql: str) -> None` | Execute a multi-statement SQL script. |
| `close` | `() -> None` | Close the database connection. |
| `table_exists` | `(table_name: str) -> bool` | Check if a table exists. |

Default pragmas: `journal_mode=wal`, `foreign_keys=on`, `busy_timeout=5000`.

### `StorageTransaction`

```python
class StorageTransaction(ABC):
    def execute(self, sql, params=()) -> Any
    def execute_many(self, sql, params_seq) -> None
    def fetch_one(self, sql, params=()) -> dict | None
    def fetch_all(self, sql, params=()) -> list[dict]
    def commit(self) -> None
    def rollback(self) -> None
```

Used as a context manager. On exception, auto-rollbacks. Caller must call `commit()` explicitly.

---

## Layer 0: Compliance Testing

```
from scoped.testing import (
    ScopedTestCase, ComplianceAuditor, IsolationFuzzer, RollbackVerifier,
    RegistryIntrospector, ComplianceMiddleware, ComplianceReport,
    ComplianceReporter, HealthChecker, HealthStatus,
    LayerSpec, ExtensionSpec, LAYER_SPECS, EXTENSION_SPECS,
    get_layer, get_all_tables, get_layers_for_invariant
)
```

### Key Classes

| Class | Purpose |
|-------|---------|
| `ScopedTestCase` | Base test case with framework fixtures |
| `ComplianceAuditor` | Validates all 10 invariants against a live backend |
| `IsolationFuzzer` | Fuzzes isolation boundaries to detect leaks |
| `RollbackVerifier` | Verifies rollback correctness across layers |
| `RegistryIntrospector` | Introspects registry state for compliance checks |
| `ComplianceMiddleware` | Runtime middleware that enforces invariants |
| `ComplianceReporter` | Generates `ComplianceReport` summaries |
| `HealthChecker` | Returns `HealthStatus` for system health |

### Manifest Functions

- `get_layer(number: int) -> LayerSpec` -- Get specification for a layer.
- `get_all_tables() -> list[str]` -- All framework table names.
- `get_layers_for_invariant(invariant: int) -> list[LayerSpec]` -- Layers that enforce a given invariant.
- `get_registry_layers() -> list[LayerSpec]` -- Layers that use the registry.
- `get_audit_layers() -> list[LayerSpec]` -- Layers that write audit entries.

---

## Layer 1: Registry

```
from scoped.registry import Registry, RegistryEntry, RegistryKind
from scoped.registry.base import get_registry, reset_global_registry
from scoped.registry.kinds import CustomKind
```

### `Registry`

```python
class Registry:
    def __init__(self) -> None
```

Thread-safe. Not a singleton by design; `get_registry()` returns the global instance.

| Method | Signature | Description |
|--------|-----------|-------------|
| `register` | `(*, kind, namespace, name, registered_by="system", target=None, metadata=None, tags=None, version=1, lifecycle=Lifecycle.ACTIVE) -> RegistryEntry` | Register a construct. Raises `AlreadyRegisteredError` on URN collision, `RegistryFrozenError` if frozen. |
| `get` | `(entry_id: str) -> RegistryEntry` | Get by ID. Raises `NotRegisteredError`. |
| `get_by_urn` | `(urn: URN \| str) -> RegistryEntry` | Get by URN. Raises `NotRegisteredError`. |
| `find_by_urn` | `(urn: URN \| str) -> RegistryEntry \| None` | Get by URN or `None`. |
| `get_by_target` | `(target: Any) -> RegistryEntry` | Get by target object reference. |
| `find_by_target` | `(target: Any) -> RegistryEntry \| None` | Get by target object or `None`. |
| `by_kind` | `(kind: RegistryKind \| CustomKind) -> list[RegistryEntry]` | All entries of a kind. |
| `by_namespace` | `(namespace: str) -> list[RegistryEntry]` | All entries in a namespace. |
| `by_tag` | `(tag: str) -> list[RegistryEntry]` | All entries with a tag. |
| `by_lifecycle` | `(lifecycle: Lifecycle) -> list[RegistryEntry]` | All entries in a lifecycle state. |
| `query` | `(*, kind=None, namespace=None, tag=None, lifecycle=None, predicate=None) -> list[RegistryEntry]` | Flexible multi-filter query. |
| `transition` | `(entry_id: str, new_lifecycle: Lifecycle) -> RegistryEntry` | Transition lifecycle, incrementing `entry_version`. |
| `archive` | `(entry_id: str) -> RegistryEntry` | Archive entry. Frees URN slot. |
| `freeze` | `() -> None` | Prevent further registrations. |
| `unfreeze` | `() -> None` | Re-allow registrations (testing). |
| `on_change` | `(callback: Callable[[str, RegistryEntry], None]) -> None` | Register mutation listener. Events: `"register"`, `"update"`, `"lifecycle_change"`. |
| `all` | `() -> list[RegistryEntry]` | All entries. |
| `count` | `() -> int` | Total entry count. |
| `contains_urn` | `(urn: URN \| str) -> bool` | Check if URN exists. |
| `clear` | `() -> None` | Remove all entries (testing only). |

### `RegistryEntry`

```python
@dataclass(slots=True)
class RegistryEntry:
    id: str
    urn: URN
    kind: RegistryKind | CustomKind
    lifecycle: Lifecycle
    registered_at: datetime
    registered_by: str
    target: Any
    metadata: Metadata
    namespace: str
    tags: set[str]
    entry_version: int = 1
    previous_entry_id: str | None = None
```

- `is_active -> bool`
- `snapshot() -> dict[str, Any]`

### `RegistryKind` (Enum)

Data/Schema: `MODEL`, `FIELD`, `RELATIONSHIP`, `INSTANCE`. Code: `FUNCTION`, `CLASS`, `METHOD`, `SIGNAL`, `TASK`. HTTP: `VIEW`, `SERIALIZER`, `MIDDLEWARE`. Framework: `PRINCIPAL`, `SCOPE`, `RULE`, `ENVIRONMENT`, `PIPELINE`, `STAGE`, `FLOW_CHANNEL`, `DEPLOYMENT`, `SECRET`, `SECRET_REF`, `INTEGRATION`, `PLUGIN`, `PLUGIN_HOOK`, `CONNECTOR`, `MARKETPLACE_LISTING`. Events/Scheduling: `EVENT_SUBSCRIPTION`, `WEBHOOK_ENDPOINT`, `NOTIFICATION_RULE`, `SCHEDULE`, `SCHEDULED_ACTION`. Other: `TEMPLATE`, `APP_CONFIG`, `MANIFEST`, `CUSTOM`.

### `CustomKind`

```python
class CustomKind:
    def __init__(self, name: str, description: str = "")
    @classmethod
    def define(cls, name: str, description: str = "") -> CustomKind
    @classmethod
    def get(cls, name: str) -> CustomKind | None
```

---

## Layer 2: Identity

```
from scoped.identity import (
    Principal, PrincipalRelationship, PrincipalStore,
    PrincipalResolver, ResolutionPath, ScopedContext
)
```

### `PrincipalStore`

```python
class PrincipalStore:
    def __init__(self, backend: StorageBackend, *, audit_writer=None) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_principal` | `(*, kind: str, display_name="", created_by="system", metadata=None, registry=None, principal_id=None) -> Principal` | Create and register a new principal. |
| `get_principal` | `(principal_id: str) -> Principal` | Fetch by ID. Raises `PrincipalNotFoundError`. |
| `find_principal` | `(principal_id: str) -> Principal \| None` | Fetch by ID or `None`. |
| `list_principals` | `(*, kind=None, lifecycle=None) -> list[Principal]` | List with optional filters. |
| `update_lifecycle` | `(principal_id: str, new_lifecycle: Lifecycle) -> Principal` | Transition lifecycle state. |
| `add_relationship` | `(*, parent_id, child_id, relationship="member_of", created_by="system", metadata=None) -> PrincipalRelationship` | Create a directed edge. |
| `remove_relationship` | `(relationship_id: str) -> None` | Delete a relationship. |
| `get_relationships` | `(principal_id, *, direction="both", relationship=None) -> list[PrincipalRelationship]` | Get relationships. `direction`: `"parent"`, `"child"`, or `"both"`. |

### `Principal`

```python
@dataclass(slots=True)
class Principal:
    id: str
    kind: str                    # application-defined: "user", "team", "org", etc.
    display_name: str
    registry_entry_id: str
    created_at: datetime
    created_by: str
    lifecycle: Lifecycle = Lifecycle.ACTIVE
    metadata: Metadata
```

- `is_active -> bool`
- `snapshot() -> dict`

### `PrincipalRelationship`

```python
@dataclass(frozen=True, slots=True)
class PrincipalRelationship:
    id: str
    parent_id: str
    child_id: str
    relationship: str            # application-defined label
    created_at: datetime
    created_by: str
    metadata: Metadata
```

### `ScopedContext`

```python
class ScopedContext:
    def __init__(self, principal: Principal, **extras: Any) -> None
```

Context manager. Every operation requires an active context.

| Method | Signature | Description |
|--------|-----------|-------------|
| `__enter__` / `__exit__` | -- | Push/pop context using `contextvars`. |
| `current` | `() -> ScopedContext` (classmethod) | Get active context. Raises `NoContextError`. |
| `current_or_none` | `() -> ScopedContext \| None` (classmethod) | Get active context or `None`. |
| `current_principal` | `() -> Principal` (classmethod) | Shortcut to get acting principal. |
| `require` | `() -> ScopedContext` (classmethod) | Alias for `current()`. |

Properties: `principal_id -> str`, `principal_kind -> str`, `extras -> dict[str, Any]`.

### `PrincipalResolver`

```python
class PrincipalResolver:
    def __init__(self, store: PrincipalStore) -> None
```

Graph walker for principal relationships. All traversals bounded by `max_depth` (default 20).

| Method | Signature | Description |
|--------|-----------|-------------|
| `ancestors` | `(principal_id, *, relationship=None, max_depth=None) -> list[Principal]` | Walk parent edges upward. |
| `descendants` | `(principal_id, *, relationship=None, max_depth=None) -> list[Principal]` | Walk child edges downward. |
| `parents` | `(principal_id, *, relationship=None) -> list[Principal]` | Immediate parents (depth=1). |
| `children` | `(principal_id, *, relationship=None) -> list[Principal]` | Immediate children (depth=1). |
| `find_path` | `(from_id, to_id, *, relationship=None, max_depth=None) -> ResolutionPath \| None` | BFS path between two principals. |
| `is_related` | `(principal_id, target_id, *, relationship=None, max_depth=None) -> bool` | Check reachability. |
| `all_related_ids` | `(principal_id, *, relationship=None, max_depth=None) -> set[str]` | All reachable principal IDs (both directions). |

### `ResolutionPath`

```python
@dataclass(frozen=True, slots=True)
class ResolutionPath:
    principals: tuple[str, ...]       # ordered principal IDs
    relationships: tuple[str, ...]    # edge labels along the path
    length -> int                     # number of edges
```

---

## Layer 3: Objects

```
from scoped.objects import (
    ScopedManager, ScopedObject, ObjectVersion, Tombstone,
    compute_checksum, diff_versions, can_access
)
```

### `ScopedManager`

```python
class ScopedManager:
    def __init__(self, backend: StorageBackend, *, audit_writer=None) -> None
```

Isolation-enforcing CRUD. All reads filtered to owner-only visibility.

| Method | Signature | Description |
|--------|-----------|-------------|
| `create` | `(*, object_type: str, owner_id: str, data: dict, registry_entry_id=None, change_reason="created") -> tuple[ScopedObject, ObjectVersion]` | Create object with first version. |
| `get` | `(object_id: str, *, principal_id: str) -> ScopedObject \| None` | Get if principal is owner; `None` otherwise. |
| `get_or_raise` | `(object_id: str, *, principal_id: str) -> ScopedObject` | Get or raise `AccessDeniedError`. |
| `list_objects` | `(*, principal_id, object_type=None, include_tombstoned=False, limit=100, offset=0) -> list[ScopedObject]` | List owner's objects. |
| `count` | `(*, principal_id, object_type=None, include_tombstoned=False) -> int` | Count owner's objects. |
| `update` | `(object_id, *, principal_id, data: dict, change_reason="") -> tuple[ScopedObject, ObjectVersion]` | Create new version. Raises `IsolationViolationError` if tombstoned. |
| `tombstone` | `(object_id, *, principal_id, reason="") -> Tombstone` | Soft-delete. Sets lifecycle to `ARCHIVED`. |
| `get_tombstone` | `(object_id: str) -> Tombstone \| None` | Get tombstone marker. |
| `get_version` | `(object_id: str, version: int) -> ObjectVersion \| None` | Get specific version (no isolation check). |
| `get_current_version` | `(object_id, *, principal_id) -> ObjectVersion \| None` | Get latest version (isolation-enforced). |
| `list_versions` | `(object_id, *, principal_id) -> list[ObjectVersion]` | All versions (isolation-enforced). |
| `diff` | `(object_id, version_a, version_b, *, principal_id) -> dict \| None` | Diff two versions. |

**Important:** `get()` enforces owner-only access. Scope projections provide visibility through the tenancy layer (`VisibilityEngine`), not through `ScopedManager`.

### `ScopedObject`

```python
@dataclass(slots=True)
class ScopedObject:
    id: str
    object_type: str
    owner_id: str
    current_version: int
    created_at: datetime
    lifecycle: Lifecycle = Lifecycle.ACTIVE
    registry_entry_id: str | None = None
```

- `is_active -> bool`
- `is_tombstoned -> bool` (lifecycle == ARCHIVED)

### `ObjectVersion`

```python
@dataclass(frozen=True, slots=True)
class ObjectVersion:
    id: str
    object_id: str
    version: int
    data: dict[str, Any]
    created_at: datetime
    created_by: str
    change_reason: str = ""
    checksum: str = ""
```

### `Tombstone`

```python
@dataclass(frozen=True, slots=True)
class Tombstone:
    id: str
    object_id: str
    tombstoned_at: datetime
    tombstoned_by: str
    reason: str = ""
```

### Utility Functions

- `compute_checksum(data: dict) -> str` -- SHA-256 of JSON-serialized data.
- `diff_versions(a: ObjectVersion, b: ObjectVersion) -> dict` -- Compute field-level diff.
- `can_access(owner_id: str, principal_id: str) -> bool` -- Returns `True` if `owner_id == principal_id`.

---

## Layer 4: Tenancy

```
from scoped.tenancy import (
    ScopeLifecycle, ProjectionManager, VisibilityEngine,
    Scope, ScopeMembership, ScopeProjection, ScopeRole, AccessLevel
)
```

### `ScopeLifecycle`

```python
class ScopeLifecycle:
    def __init__(self, backend: StorageBackend, *, audit_writer=None) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_scope` | `(*, name, owner_id, description="", parent_scope_id=None, metadata=None) -> Scope` | Create scope. Owner auto-added as `OWNER` member. |
| `get_scope` | `(scope_id: str) -> Scope \| None` | Get by ID. |
| `get_scope_or_raise` | `(scope_id: str) -> Scope` | Get or raise `ScopeNotFoundError`. |
| `list_scopes` | `(*, owner_id=None, parent_scope_id=None, include_archived=False) -> list[Scope]` | List scopes. |
| `add_member` | `(scope_id, *, principal_id, role=ScopeRole.VIEWER, granted_by, expires_at=None) -> ScopeMembership` | Add member. Raises `ScopeFrozenError` if frozen/archived. |
| `revoke_member` | `(scope_id, *, principal_id, revoked_by, role=None) -> int` | Revoke membership. Returns count revoked. If `role` given, only that role. |
| `get_memberships` | `(scope_id, *, active_only=True) -> list[ScopeMembership]` | List scope members. |
| `get_principal_scopes` | `(principal_id, *, active_only=True) -> list[ScopeMembership]` | List scopes a principal belongs to. |
| `is_member` | `(scope_id, principal_id) -> bool` | Check active membership. |
| `freeze_scope` | `(scope_id, *, frozen_by) -> Scope` | Freeze scope (no further changes). |
| `archive_scope` | `(scope_id, *, archived_by) -> Scope` | Dissolve scope. Archives all memberships and projections. |

### `ProjectionManager`

```python
class ProjectionManager:
    def __init__(self, backend: StorageBackend, *, audit_writer=None) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `project` | `(*, scope_id, object_id, projected_by, access_level=AccessLevel.READ) -> ScopeProjection` | Project object into scope. Only owner can project. |
| `revoke_projection` | `(*, scope_id, object_id, revoked_by) -> bool` | Revoke projection. Returns `True` if revoked. |
| `get_projections` | `(scope_id, *, active_only=True) -> list[ScopeProjection]` | All projections in a scope. |
| `get_object_projections` | `(object_id, *, active_only=True) -> list[ScopeProjection]` | All scopes an object is projected into. |
| `is_projected` | `(scope_id, object_id) -> bool` | Check if object has active projection. |

### `VisibilityEngine`

```python
class VisibilityEngine:
    def __init__(self, backend: StorageBackend) -> None
```

Resolves "what can principal X see?" by combining ownership, scope membership, projections, and scope hierarchy.

| Method | Signature | Description |
|--------|-----------|-------------|
| `visible_object_ids` | `(principal_id, *, object_type=None, limit=1000) -> list[str]` | IDs of all visible objects (owned + projected). |
| `can_see` | `(principal_id, object_id) -> bool` | Check visibility for a specific object. |
| `get_access_level` | `(principal_id, object_id) -> AccessLevel \| None` | Highest access level. Owner gets `ADMIN`. `None` if not visible. |
| `scope_member_ids` | `(scope_id) -> list[str]` | Active member principal IDs. |
| `ancestor_scope_ids` | `(scope_id, *, max_depth=20) -> list[str]` | Walk scope hierarchy upward. |
| `descendant_scope_ids` | `(scope_id, *, max_depth=20) -> list[str]` | Walk scope hierarchy downward. |

### Enums

**`ScopeRole`:** `VIEWER`, `EDITOR`, `ADMIN`, `OWNER`

**`AccessLevel`:** `READ`, `WRITE`, `ADMIN`

### Models

**`Scope`:** `id`, `name`, `owner_id`, `created_at`, `description`, `parent_scope_id`, `registry_entry_id`, `lifecycle`, `metadata`. Properties: `is_active`, `is_frozen`, `is_archived`.

**`ScopeMembership`:** `id`, `scope_id`, `principal_id`, `role: ScopeRole`, `granted_at`, `granted_by`, `expires_at`, `lifecycle`.

**`ScopeProjection`:** `id`, `scope_id`, `object_id`, `projected_at`, `projected_by`, `access_level: AccessLevel`, `lifecycle`.

---

## Layer 5: Rules

```
from scoped.rules import (
    RuleStore, RuleEngine, Rule, RuleBinding, RuleVersion,
    RuleType, RuleEffect, BindingTargetType, EvaluationResult,
    RuleCompiler, CompiledRuleSet
)
```

### `RuleStore`

```python
class RuleStore:
    def __init__(self, backend: StorageBackend, *, audit_writer=None) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_rule` | `(*, name, rule_type: RuleType, effect: RuleEffect, conditions=None, priority=0, description="", created_by) -> Rule` | Create rule with first version. |
| `get_rule` | `(rule_id) -> Rule \| None` | Get by ID. |
| `list_rules` | `(*, rule_type=None, effect=None, active_only=True) -> list[Rule]` | List with filters. |
| `update_rule` | `(rule_id, *, updated_by, conditions=None, effect=None, priority=None, change_reason="") -> Rule` | Update rule, creating new version. |
| `archive_rule` | `(rule_id, *, archived_by) -> Rule` | Soft-delete rule and its bindings. |
| `get_versions` | `(rule_id) -> list[RuleVersion]` | All versions of a rule. |
| `bind_rule` | `(rule_id, *, target_type: BindingTargetType, target_id, bound_by) -> RuleBinding` | Bind rule to a target. |
| `unbind_rule` | `(rule_id, *, target_type, target_id) -> bool` | Remove binding. Returns `True` if removed. |
| `get_bindings` | `(rule_id, *, active_only=True) -> list[RuleBinding]` | Bindings for a rule. |
| `get_target_bindings` | `(target_type, target_id, *, active_only=True) -> list[RuleBinding]` | All bindings for a target. |

### `RuleEngine`

```python
class RuleEngine:
    def __init__(self, backend: StorageBackend, *, audit_writer=None) -> None
```

Deny-overrides evaluation model: any DENY wins over any ALLOWs. Default-deny when no rules match.

| Method | Signature | Description |
|--------|-----------|-------------|
| `evaluate` | `(*, action: str, principal_id=None, principal_kind=None, object_type=None, object_id=None, scope_id=None) -> EvaluationResult` | Evaluate rules for an access request. |

### `EvaluationResult`

```python
@dataclass(frozen=True, slots=True)
class EvaluationResult:
    allowed: bool
    matching_rules: tuple[Rule, ...]
    deny_rules: tuple[Rule, ...]
    allow_rules: tuple[Rule, ...]
```

`bool(result)` returns `result.allowed`.

### Enums

**`RuleType`:** `ACCESS`, `SHARING`, `VISIBILITY`, `OWNERSHIP`, `CONSTRAINT`, `REDACTION`, `RATE_LIMIT`, `QUOTA`, `FEATURE_FLAG`

**`RuleEffect`:** `ALLOW`, `DENY`

**`BindingTargetType`:** `SCOPE`, `PRINCIPAL`, `OBJECT_TYPE`, `OBJECT`, `ENVIRONMENT`, `CONNECTOR`

### Rule Conditions

Conditions are a JSON dict with optional matcher fields. Empty conditions match everything.

```python
{
    "action": ["read", "update"],           # list or single value
    "principal_kind": ["user"],
    "object_type": ["document"],
    "scope_id": ["scope-id-here"]
}
```

---

## Layer 6: Audit

```
from scoped.audit import AuditWriter, AuditQuery, TraceEntry, ChainVerification, compute_hash
```

### `AuditWriter`

```python
class AuditWriter:
    def __init__(self, backend: StorageBackend, *, hash_algorithm="sha256") -> None
```

Append-only, thread-safe writer. Maintains hash chain in memory, seeded from DB.

| Method | Signature | Description |
|--------|-----------|-------------|
| `record` | `(*, actor_id, action: ActionType, target_type, target_id, scope_id=None, before_state=None, after_state=None, metadata=None, parent_trace_id=None) -> TraceEntry` | Record a single trace entry. |
| `record_batch` | `(entries: list[dict]) -> list[TraceEntry]` | Record multiple entries atomically. Each dict has the same kwargs as `record()`. |

Properties: `last_sequence -> int`, `last_hash -> str`.

### `AuditQuery`

```python
class AuditQuery:
    def __init__(self, backend: StorageBackend, *, hash_algorithm="sha256") -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `get` | `(entry_id: str) -> TraceEntry \| None` | Fetch by ID. |
| `get_by_sequence` | `(sequence: int) -> TraceEntry \| None` | Fetch by sequence number. |
| `query` | `(*, actor_id=None, action=None, target_type=None, target_id=None, scope_id=None, parent_trace_id=None, since=None, until=None, limit=100, offset=0) -> list[TraceEntry]` | Filtered query, ordered by sequence ASC. |
| `count` | `(*, actor_id=None, action=None, target_type=None, target_id=None) -> int` | Count matching entries. |
| `children` | `(parent_trace_id: str) -> list[TraceEntry]` | Child traces of a parent. |
| `history` | `(target_type, target_id, *, limit=100) -> list[TraceEntry]` | Full trace history for a target. |
| `verify_chain` | `(*, from_sequence=1, to_sequence=None) -> ChainVerification` | Verify hash chain integrity. |

### `TraceEntry`

```python
@dataclass(slots=True)
class TraceEntry:
    id: str
    sequence: int
    actor_id: str
    action: ActionType
    target_type: str
    target_id: str
    timestamp: datetime
    hash: str
    previous_hash: str = ""
    scope_id: str | None = None
    before_state: dict | None = None
    after_state: dict | None = None
    metadata: dict = {}
    parent_trace_id: str | None = None
```

### `ChainVerification`

```python
class ChainVerification:
    valid: bool
    entries_checked: int
    first_sequence: int
    last_sequence: int
    broken_at_sequence: int | None = None
```

### Hash Chain

Each `TraceEntry.hash` is computed from its content plus the `previous_hash` of the preceding entry, forming a tamper-evident chain. `compute_hash(entry_id, sequence, actor_id, action, target_type, target_id, timestamp, previous_hash, algorithm="sha256") -> str`.

---

## Layer 7: Temporal

```
from scoped.temporal import (
    RollbackExecutor, RollbackResult, StateReconstructor, RollbackConstraintChecker
)
```

### `RollbackExecutor`

```python
class RollbackExecutor:
    def __init__(
        self, backend: StorageBackend, *,
        audit_writer: AuditWriter,
        constraint_checker: RollbackConstraintChecker | None = None
    ) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `rollback_action` | `(trace_id, *, actor_id, principal_kind=None, reason="") -> RollbackResult` | Reverse a single traced action. Restores `before_state`. |
| `rollback_to_timestamp` | `(target_type, target_id, at: datetime, *, actor_id, principal_kind=None, reason="") -> RollbackResult` | Restore target to state at timestamp. Rolls back all entries after `at` in reverse order. |
| `rollback_cascade` | `(trace_id, *, actor_id, principal_kind=None, reason="") -> RollbackResult` | Rollback an action and all dependent actions (via `parent_trace_id` chain). |

### `RollbackResult`

```python
@dataclass(frozen=True, slots=True)
class RollbackResult:
    success: bool
    rolled_back: tuple[str, ...]         # trace IDs rolled back
    rollback_trace_ids: tuple[str, ...]  # trace IDs of rollback entries created
    skipped: tuple[str, ...] = ()
    denied: tuple[str, ...] = ()
```

`bool(result)` returns `result.success`.

### `StateReconstructor`

```python
class StateReconstructor:
    def __init__(self, backend: StorageBackend) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `reconstruct` | `(target_type, target_id, at: datetime) -> ReconstructedState` | Rebuild entity state at a point in time using audit trail `after_state` snapshots. |

### `ReconstructedState`

```python
@dataclass(frozen=True, slots=True)
class ReconstructedState:
    target_type: str
    target_id: str
    timestamp: datetime
    state: dict | None
    trace_id: str | None
    found: bool
```

### `RollbackConstraintChecker`

Pluggable constraint checker that can block rollbacks based on rules, dependencies, or custom logic.

---

## Layer 8: Environments

```
from scoped.environments import (
    EnvironmentLifecycle, EnvironmentContainer, SnapshotManager,
    Environment, EnvironmentObject, EnvironmentSnapshot, EnvironmentState,
    EnvironmentTemplate, ObjectOrigin
)
```

### `EnvironmentLifecycle`

```python
class EnvironmentLifecycle:
    def __init__(self, backend: StorageBackend, *, audit_writer=None) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `spawn` | `(*, name, owner_id, description="", template_id=None, ephemeral=True, metadata=None) -> Environment` | Spawn in `SPAWNING` state with auto-created isolation scope. |
| `activate` | `(env_id, *, actor_id) -> Environment` | `SPAWNING` -> `ACTIVE`. |
| `suspend` | `(env_id, *, actor_id) -> Environment` | `ACTIVE` -> `SUSPENDED`. |
| `resume` | `(env_id, *, actor_id) -> Environment` | `SUSPENDED` -> `ACTIVE`. |
| `complete` | `(env_id, *, actor_id) -> Environment` | `ACTIVE` -> `COMPLETED`. |
| `discard` | `(env_id, *, actor_id) -> Environment` | `COMPLETED`/`PROMOTED` -> `DISCARDED`. Archives the scope. |
| `promote` | `(env_id, *, actor_id) -> Environment` | `COMPLETED` -> `PROMOTED`. |
| `get` | `(env_id) -> Environment \| None` | Get by ID. |

### `EnvironmentState` (Enum)

`SPAWNING`, `ACTIVE`, `SUSPENDED`, `COMPLETED`, `DISCARDED`, `PROMOTED`

Valid transitions: `SPAWNING` -> `ACTIVE` -> `SUSPENDED`/`COMPLETED`. `SUSPENDED` -> `ACTIVE`. `COMPLETED` -> `DISCARDED`/`PROMOTED`. `PROMOTED` -> `DISCARDED`.

### `EnvironmentContainer`

Manages objects within an environment (add, remove, list objects in the env scope).

### `SnapshotManager`

Creates and restores point-in-time snapshots of environment state.

---

## Layer 9: Flow

```
from scoped.flow import (
    PipelineManager, FlowEngine, PromotionManager, FlowResolution,
    Pipeline, Stage, StageTransition, FlowChannel, Promotion, FlowPointType
)
```

### `PipelineManager`

```python
class PipelineManager:
    def __init__(self, backend: StorageBackend, *, audit_writer=None) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_pipeline` | `(*, name, owner_id, description="") -> Pipeline` | Create a new pipeline. |
| `get_pipeline` | `(pipeline_id) -> Pipeline \| None` | Get by ID. |
| `add_stage` | `(*, pipeline_id, name, order, ...) -> Stage` | Add a stage to a pipeline. |
| `transition` | `(*, object_id, from_stage_id, to_stage_id, actor_id, ...) -> StageTransition` | Move object between stages. |

### `FlowEngine`

```python
class FlowEngine:
    def __init__(self, backend: StorageBackend, *, audit_writer=None) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_channel` | `(*, name, source_type: FlowPointType, source_id, target_type: FlowPointType, target_id, owner_id, allowed_types=None) -> FlowChannel` | Create a flow channel. |
| `can_flow` | `(source_type, source_id, target_type, target_id, ...) -> FlowResolution` | Check if flow is permitted. |

### `PromotionManager`

```python
class PromotionManager:
    def __init__(self, backend: StorageBackend, *, flow_engine=None, audit_writer=None) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `promote` | `(*, object_id, source_env_id, target_scope_id, promoted_by, target_stage_id=None, object_type=None) -> Promotion` | Promote object from environment into a scope. Checks flow channels if engine configured. |

### `FlowPointType` (Enum)

`SCOPE`, `ENVIRONMENT`, `STAGE`, `PIPELINE`, `EXTERNAL`

### `FlowResolution`

```python
@dataclass(frozen=True, slots=True)
class FlowResolution:
    allowed: bool
    channel: FlowChannel | None = None
    reason: str = ""
```

---

## Layer 10: Deployments

```
from scoped.deployments import (
    DeploymentExecutor, GateChecker, GateResult, DeploymentRollbackManager,
    Deployment, DeploymentTarget, DeploymentGate, DeploymentState, GateType
)
```

### `DeploymentExecutor`

```python
class DeploymentExecutor:
    def __init__(self, backend: StorageBackend, *, audit_writer=None) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_target` | `(*, name, target_type, owner_id, config=None) -> DeploymentTarget` | Register a deployment destination. |
| `get_target` | `(target_id) -> DeploymentTarget \| None` | Get target by ID. |
| `create_deployment` | `(*, target_id, object_ids, version, deployed_by, ...) -> Deployment` | Create a deployment record. |
| `advance_state` | `(deployment_id, new_state, ...) -> Deployment` | Transition deployment state. |

### `GateChecker`

Pre-deployment gate checks. Evaluates gates (stage checks, rule checks, approvals, custom) before allowing deployment.

### `DeploymentRollbackManager`

Rolls back deployments to previous versions.

### `DeploymentState` (Enum)

`PENDING`, `DEPLOYING`, `DEPLOYED`, `FAILED`, `ROLLED_BACK`

### `GateType` (Enum)

`STAGE_CHECK`, `RULE_CHECK`, `APPROVAL`, `CUSTOM`

---

## Layer 11: Secrets

```
from scoped.secrets import (
    SecretVault, Secret, SecretVersion, SecretRef, SecretAccessEntry,
    AccessResult, SecretClassification, SecretPolicy, SecretPolicyManager,
    SecretBackend, InMemoryBackend, FernetBackend, LeakDetector
)
```

### `SecretVault`

```python
class SecretVault:
    def __init__(
        self, backend: StorageBackend, encryption: SecretBackend, *,
        object_manager: ScopedManager | None = None, audit_writer=None
    ) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_secret` | `(*, name, plaintext_value, owner_id, description="", classification="standard", expires_at=None, key_id=None) -> tuple[Secret, SecretVersion]` | Create encrypted secret with first version. |
| `get_secret` | `(secret_id) -> Secret \| None` | Get metadata (never the value). |
| `get_secret_or_raise` | `(secret_id) -> Secret` | Get or raise `SecretNotFoundError`. |
| `list_secrets` | `(*, owner_id=None, classification=None, active_only=True, limit=100) -> list[Secret]` | List secrets. |
| `archive_secret` | `(secret_id, *, actor_id) -> None` | Archive secret and revoke all refs. |
| `rotate` | `(secret_id, *, new_value, rotated_by, reason="rotation", key_id=None) -> SecretVersion` | Rotate to new value. Old version kept. |
| `get_versions` | `(secret_id) -> list[SecretVersion]` | All versions. |
| `get_version` | `(secret_id, version: int) -> SecretVersion \| None` | Specific version. |
| `grant_ref` | `(*, secret_id, granted_to, granted_by, scope_id=None, environment_id=None, expires_at=None) -> SecretRef` | Grant opaque reference token. |
| `resolve_ref` | `(ref_token, *, principal_id) -> AccessResult` | Resolve ref to plaintext (verified access). |
| `revoke_ref` | `(ref_id, *, revoked_by) -> None` | Revoke a ref. |

### Encryption Backends

- `InMemoryBackend` -- For testing. Stores keys in memory.
- `FernetBackend` -- Production. Uses Fernet symmetric encryption.

Both implement `SecretBackend` protocol: `generate_key()`, `encrypt(plaintext, key_id)`, `decrypt(ciphertext, key_id)`.

### `SecretClassification` (Enum)

Classification levels for secrets (standard, sensitive, critical, etc.).

### `LeakDetector`

Scans data for potential secret leaks (plaintext values appearing where they shouldn't).

**Invariant:** Secret values never appear in audit trails, snapshots, or connector traffic.

---

## Layer 12: Integrations

```
from scoped.integrations import (
    IntegrationManager, PluginLifecycleManager, PluginSandbox,
    HookRegistry, HookResult, DispatchResult,
    Integration, Plugin, PluginHook, PluginPermission, PluginState
)
```

### `IntegrationManager`

```python
class IntegrationManager:
    def __init__(self, backend: StorageBackend, *, audit_writer=None) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_integration` | `(*, name, integration_type, owner_id, description="", scope_id=None, config=None, credentials_ref=None, metadata=None) -> Integration` | Register external system connection. |
| `get_integration` | `(integration_id) -> Integration \| None` | Get by ID. |
| `disconnect` | `(integration_id, *, actor_id) -> None` | Disconnect (archive). |

### `PluginLifecycleManager`

Manages plugin lifecycle: install, activate, suspend, uninstall.

### `PluginSandbox`

Runs plugin code in a restricted environment with explicit permission grants.

### `HookRegistry`

```python
class HookRegistry:
    def register_hook(self, hook_point: str, callback, ...) -> PluginHook
    def dispatch(self, hook_point: str, context: dict) -> DispatchResult
```

### `PluginState` (Enum)

`INSTALLED`, `ACTIVE`, `SUSPENDED`, `UNINSTALLED`

---

## Layer 13: Connector

```
from scoped.connector import (
    ConnectorManager, Connector, ConnectorState, ConnectorDirection,
    ConnectorPolicy, ConnectorTraffic, PolicyType, TrafficStatus,
    FederationProtocol, FederationMessage, NegotiationResult, SchemaCapability
)
from scoped.connector.marketplace import (
    MarketplacePublisher, MarketplaceDiscovery,
    MarketplaceListing, MarketplaceInstall, MarketplaceReview,
    ListingType, Visibility
)
```

### `ConnectorManager`

```python
class ConnectorManager:
    def __init__(self, backend: StorageBackend, *, audit_writer=None) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `propose` | `(*, name, local_org_id, remote_org_id, remote_endpoint, created_by, description="", direction=ConnectorDirection.BIDIRECTIONAL, metadata=None) -> Connector` | Propose new connector (state=`PROPOSED`). |
| `approve` | `(connector_id, *, approved_by) -> Connector` | Approve connector. |
| `activate` | `(connector_id, *, actor_id) -> Connector` | Activate for traffic. |
| `revoke` | `(connector_id, *, revoked_by) -> Connector` | Revoke connector. |
| `add_policy` | `(*, connector_id, policy_type, ...) -> ConnectorPolicy` | Add traffic policy. |
| `send` | `(connector_id, *, payload, sent_by) -> ConnectorTraffic` | Send data through connector. |

### `ConnectorState` (Enum)

`PROPOSED`, `APPROVED`, `ACTIVE`, `SUSPENDED`, `REVOKED`

### `ConnectorDirection` (Enum)

`INBOUND`, `OUTBOUND`, `BIDIRECTIONAL`

### `PolicyType` (Enum)

Types of policies governing connector traffic.

### `FederationProtocol`

Handles schema negotiation and message exchange between organizations.

### Marketplace

- `MarketplacePublisher` -- Publish listings (plugins, templates, connectors).
- `MarketplaceDiscovery` -- Search and browse listings.
- `ListingType` -- `PLUGIN`, `TEMPLATE`, `CONNECTOR`, etc.
- `Visibility` -- `PUBLIC`, `PRIVATE`, `ORGANIZATION`.

---

## Layer 14: Events

```
from scoped.events import (
    EventBus, SubscriptionManager, WebhookDelivery,
    Event, EventType, EventSubscription, WebhookEndpoint,
    DeliveryAttempt, DeliveryStatus
)
```

### `EventBus`

```python
class EventBus:
    def __init__(self, backend: StorageBackend, *, audit_writer=None) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `emit` | `(event_type: EventType, *, actor_id, target_type, target_id, scope_id=None, data=None) -> Event` | Emit an event. Persists, matches subscriptions, queues webhooks, calls listeners. |
| `on` | `(event_type: EventType \| str, listener: Callable[[Event], None]) -> None` | Register in-process listener. |
| `off` | `(event_type: EventType \| str, listener) -> None` | Remove listener. |
| `get_event` | `(event_id) -> Event \| None` | Get event by ID. |
| `query_events` | `(*, event_type=None, actor_id=None, scope_id=None, ...) -> list[Event]` | Query events. |

### `SubscriptionManager`

```python
class SubscriptionManager:
    def __init__(self, backend: StorageBackend, *, audit_writer=None) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_webhook` | `(*, name, url, owner_id, config=None, scope_id=None) -> WebhookEndpoint` | Register webhook endpoint. |
| `subscribe` | `(*, event_types, owner_id, ...) -> EventSubscription` | Create event subscription. |
| `unsubscribe` | `(subscription_id, *, actor_id) -> None` | Remove subscription. |

### `WebhookDelivery`

Manages webhook delivery attempts with retry logic.

### `EventType` (Enum)

`OBJECT_CREATED`, `OBJECT_UPDATED`, `OBJECT_DELETED`, `SCOPE_CREATED`, `SCOPE_MODIFIED`, `SCOPE_DISSOLVED`, `MEMBERSHIP_CHANGED`, `RULE_CHANGED`, `ENVIRONMENT_SPAWNED`, `ENVIRONMENT_COMPLETED`, `ENVIRONMENT_DISCARDED`, `ENVIRONMENT_PROMOTED`, `DEPLOYMENT_COMPLETED`, `DEPLOYMENT_ROLLED_BACK`, `SECRET_ROTATED`, `STAGE_TRANSITIONED`, `CONNECTOR_SYNCED`, `CUSTOM`

### `DeliveryStatus` (Enum)

`PENDING`, `DELIVERED`, `FAILED`, `RETRYING`

---

## Layer 15: Notifications

```
from scoped.notifications import (
    NotificationEngine, DeliveryManager, PreferenceManager,
    Notification, NotificationRule, NotificationChannel,
    NotificationStatus, NotificationPreference
)
```

### `NotificationEngine`

```python
class NotificationEngine:
    def __init__(self, backend: StorageBackend, *, audit_writer=None) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_rule` | `(*, name, owner_id, event_types=None, target_types=None, scope_id=None, recipient_ids: list[str], channel=NotificationChannel.IN_APP, title_template="{event_type}", body_template="{target_type} {target_id}") -> NotificationRule` | Create notification rule. |
| `get_rule` | `(rule_id) -> NotificationRule \| None` | Get rule by ID. |
| `list_rules` | `(*, owner_id=None) -> list[NotificationRule]` | List rules. |
| `archive_rule` | `(rule_id, *, actor_id) -> None` | Archive rule. |
| `process_event` | `(event: Event) -> list[Notification]` | Match event against rules and generate notifications. |
| `get_notifications` | `(recipient_id, *, status=None, limit=50) -> list[Notification]` | Get notifications for a principal. |
| `mark_read` | `(notification_id, *, actor_id) -> None` | Mark notification as read. |
| `dismiss` | `(notification_id, *, actor_id) -> None` | Dismiss notification. |

### `NotificationChannel` (Enum)

`IN_APP`, `EMAIL`, `SMS`, `PUSH`, `WEBHOOK`

### `NotificationStatus` (Enum)

`UNREAD`, `READ`, `DISMISSED`

### `DeliveryManager`

Handles actual delivery of notifications through configured channels.

### `PreferenceManager`

Per-principal notification preferences (opt-in/opt-out per channel/event type).

---

## Layer 16: Scheduling

```
from scoped.scheduling import (
    Scheduler, JobQueue, ScheduledAction, RecurringSchedule,
    Job, JobState
)
```

### `Scheduler`

```python
class Scheduler:
    def __init__(self, backend: StorageBackend, *, audit_writer=None) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_schedule` | `(*, name, owner_id, cron_expression=None, interval_seconds=None) -> RecurringSchedule` | Create recurring schedule. Exactly one of cron or interval required. |
| `create_action` | `(*, name, owner_id, action_type, action_config, next_run_at, schedule_id=None, scope_id=None) -> ScheduledAction` | Create a scheduled action. |
| `get_due_actions` | `(as_of: datetime = None) -> list[ScheduledAction]` | Get actions due for execution. |
| `archive_action` | `(action_id, *, actor_id) -> None` | Archive a scheduled action. |

### `JobQueue`

```python
class JobQueue:
    def __init__(self, backend: StorageBackend, *, executor: JobExecutor | None = None) -> None
```

`JobExecutor` signature: `(action_type: str, action_config: dict) -> dict[str, Any]`

| Method | Signature | Description |
|--------|-----------|-------------|
| `enqueue` | `(*, name, action_type, action_config=None, owner_id, scheduled_action_id=None, scope_id=None) -> Job` | Create job in `QUEUED` state. |
| `run_next` | `() -> Job \| None` | Execute the next queued job. |
| `get_job` | `(job_id) -> Job \| None` | Get job by ID. |
| `cancel` | `(job_id) -> Job` | Cancel a queued job. |

### `JobState` (Enum)

`QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`

### Models

**`ScheduledAction`:** `id`, `name`, `owner_id`, `action_type`, `action_config`, `next_run_at`, `schedule_id`, `scope_id`, `created_at`, `lifecycle`.

**`RecurringSchedule`:** `id`, `name`, `owner_id`, `cron_expression`, `interval_seconds`, `created_at`, `lifecycle`. Standard 5-field cron syntax: `minute hour day_of_month month day_of_week`.

**`Job`:** `id`, `name`, `action_type`, `action_config`, `owner_id`, `state: JobState`, `created_at`, `started_at`, `completed_at`, `result`, `error`, `scheduled_action_id`, `scope_id`, `lifecycle`.

---

## Extensions

### A1: Migrations

```
from scoped.storage import MigrationRunner, MigrationRegistry, BaseMigration, MigrationRecord, MigrationStatus
```

- `MigrationRegistry` -- Register versioned migrations.
- `MigrationRunner` -- Apply/rollback migrations in order.
- `BaseMigration` -- Base class for migration definitions. Implement `up(backend)` and `down(backend)`.
- `MigrationStatus` -- Track which migrations have been applied.

### A2: Contracts

```
from scoped.registry import ContractStore, Contract, ContractField, ContractVersion, ContractConstraint, FieldType, ValidationResult, diff_contracts, validate_against_version
```

**`ContractStore`:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_contract` | `(*, name, object_type, owner_id, fields: list[ContractField], description="", constraints=None) -> Contract` | Define schema for an object type. |
| `get_contract` | `(contract_id) -> Contract \| None` | Get by ID. |
| `get_contract_or_raise` | `(contract_id) -> Contract` | Get or raise `ContractNotFoundError`. |
| `get_contract_for_type` | `(object_type) -> Contract \| None` | Active contract for an object type. |
| `validate` | `(data, contract_id, *, version=None) -> ValidationResult` | Validate data against a contract version. |
| `validate_for_type` | `(data, object_type) -> ValidationResult` | Validate against active contract for a type. |

**`FieldType`** (Enum): `STRING`, `INTEGER`, `FLOAT`, `BOOLEAN`, `DATETIME`, `JSON`, `LIST`, `REF`, `BLOB`, `ANY`

**`ContractField`:** `name`, `field_type: FieldType`, `required=True`, `default=None`, `description=""`, `constraints={}`.

**`ValidationResult`:** `valid: bool`, `errors: list[str]`.

- `diff_contracts(a, b)` -- Diff two contract versions.

### A3: Rule Extensions

```
from scoped.rules import (
    RedactionEngine, FieldRedaction, RedactionResult, RedactionStrategy,
    RateLimitChecker, RateLimitConfig, RateLimitResult,
    QuotaChecker, QuotaConfig, QuotaResult,
    FeatureFlagEngine, FeatureFlagConfig, FeatureFlagResult,
    RuleCompiler, CompiledRuleSet
)
```

- **`RedactionEngine`** -- Apply field-level redaction based on rules. Strategies: mask, hash, remove, etc.
- **`RateLimitChecker`** -- Check rate limits for principals/actions.
- **`QuotaChecker`** -- Check resource quotas (object counts, storage, etc.).
- **`FeatureFlagEngine`** -- Evaluate feature flags per principal/scope.
- **`RuleCompiler`** / **`CompiledRuleSet`** -- Pre-compile rules for faster evaluation.

### A4: Blobs

```
from scoped.objects import BlobManager, BlobRef, BlobVersion
from scoped.storage import BlobBackend, InMemoryBlobBackend, LocalBlobBackend
```

**`BlobManager`:**

```python
class BlobManager:
    def __init__(self, backend: StorageBackend, blob_backend: BlobBackend, *, audit_writer=None) -> None
```

| Method | Signature | Description |
|--------|-----------|-------------|
| `store` | `(*, data: bytes, filename, content_type, owner_id, object_id=None, metadata=None) -> BlobRef` | Store binary content. Returns reference handle. |
| `read` | `(blob_id, *, principal_id) -> bytes` | Read blob content (isolation-enforced). |
| `update` | `(blob_id, *, data: bytes, principal_id, ...) -> BlobRef` | Update blob, creating new version. |
| `get_or_raise` | `(blob_id, *, principal_id) -> BlobRef` | Get ref or raise `AccessDeniedError`. |

Blob backends: `InMemoryBlobBackend` (testing), `LocalBlobBackend` (filesystem).

### A5: Config Hierarchy

```
from scoped.tenancy import ConfigStore, ConfigResolver, ScopedSetting, ResolvedSetting
```

**`ConfigStore`** -- CRUD for per-scope key-value settings.

**`ConfigResolver`** -- Resolves settings through scope hierarchy. Child scopes inherit parent settings unless overridden.

**`ResolvedSetting`:** `key`, `value`, `source_scope_id`, `inherited: bool`.

### A6: Search

```
from scoped.objects import SearchIndex, SearchResult, IndexEntry
```

**`SearchIndex`:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `index_object` | `(*, object_id, object_type, owner_id, data: dict, scope_id=None) -> list[IndexEntry]` | Index object fields for full-text search. |
| `reindex_object` | `(*, object_id, object_type, owner_id, data: dict, scope_id=None) -> list[IndexEntry]` | Remove old entries and re-index. |
| `search` | `(query, *, principal_id, object_type=None, scope_id=None, limit=50) -> list[SearchResult]` | Owner-filtered full-text search. |
| `search_with_visibility` | `(query, *, principal_id, visible_object_ids, ...) -> list[SearchResult]` | Search with explicit visibility list (from `VisibilityEngine`). |

Uses SQLite FTS5.

### A7: Templates

```
from scoped.registry import TemplateStore, Template, TemplateVersion, InstantiationResult
```

**`TemplateStore`:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `create_template` | `(*, name, template_type, owner_id, schema: dict, description="", scope_id=None) -> Template` | Create reusable blueprint. |
| `get_template` | `(template_id) -> Template` | Get or raise `TemplateNotFoundError`. |
| `update_template` | `(template_id, *, principal_id, schema, name=None, description=None, change_reason="") -> Template` | Update template, creating new version. |
| `instantiate` | `(template_id, *, overrides=None, version=None) -> InstantiationResult` | Instantiate template with merged defaults + overrides. |

`template_type` examples: `"scope"`, `"environment"`, `"object"`, `"pipeline"`, `"rule_set"`.

### A8: Tiering

```
from scoped.storage import TierManager, StorageTier, TierAssignment, RetentionPolicy, TierTransitionCandidate
```

- **`TierManager`** -- Manage storage tiers (hot, warm, cold, glacial).
- **`StorageTier`** -- Tier definitions.
- **`RetentionPolicy`** -- When to transition data between tiers.
- **`TierTransitionCandidate`** -- Objects eligible for tier migration.

Also: `ArchiveManager`, `GlacialArchive` for long-term archival.

### A9: Import/Export

```
from scoped.objects import Exporter, ExportPackage, Importer, ImportResult
```

**`Exporter`:**

```python
class Exporter:
    def __init__(self, backend: StorageBackend) -> None
    def export_objects(self, *, principal_id, object_type=None, ...) -> ExportPackage
```

**`Importer`:**

```python
class Importer:
    def __init__(self, backend: StorageBackend) -> None
    def import_package(self, package: ExportPackage, *, principal_id, object_type_filter=None, recompute_checksums=True) -> ImportResult
```

**`ImportResult`:** `imported_count`, `skipped_count`, `version_count`, `id_mapping: dict[str, str]` (old_id -> new_id), `errors: list[str]`.

Imports create new objects with new IDs. All imported objects are owned by the importing principal.

---

## Framework Adapters

All adapters are optional. Install with extras: `pip install pyscoped[django]`, etc.

### Django (`scoped.contrib.django`)

```python
# settings.py
INSTALLED_APPS = ["scoped.contrib.django"]
MIDDLEWARE = ["scoped.contrib.django.middleware.ScopedContextMiddleware"]
SCOPED_PRINCIPAL_HEADER = "HTTP_X_SCOPED_PRINCIPAL_ID"
```

Management commands: `scoped_health`, `scoped_audit`, `scoped_compliance`.

### FastAPI (`scoped.contrib.fastapi`)

```python
from scoped.contrib.fastapi.middleware import ScopedContextMiddleware
from scoped.contrib.fastapi.router import router as scoped_router
from scoped.contrib.fastapi.dependencies import get_scoped_context, get_principal

app.add_middleware(ScopedContextMiddleware, backend=backend)
app.include_router(scoped_router)  # /scoped/health, /scoped/audit
```

Dependencies: `get_scoped_context`, `get_principal` for injection into route handlers.

### Flask (`scoped.contrib.flask`)

```python
from scoped.contrib.flask.extension import ScopedExtension
from scoped.contrib.flask.admin import admin_bp

scoped = ScopedExtension(app)        # auto-inits backend, injects g.scoped_context
app.register_blueprint(admin_bp)     # /scoped/health, /scoped/audit
```

### MCP (`scoped.contrib.mcp`)

```python
from scoped.contrib.mcp.server import create_scoped_server

mcp = create_scoped_server(backend)
mcp.run()
```

Tools: `create_principal`, `create_object`, `get_object`, `create_scope`, `list_audit`, `health_check`.

Resources: `scoped://principals`, `scoped://health`, `scoped://audit/recent`.

---

## Exceptions

All exceptions inherit from `ScopedError` and carry structured `context: dict[str, Any]`.

| Exception | Layer | Description |
|-----------|-------|-------------|
| `ScopedError` | Base | Base for all framework errors |
| `NotRegisteredError` | 1 | Construct not in registry |
| `AlreadyRegisteredError` | 1 | URN collision |
| `RegistryFrozenError` | 1 | Registry is frozen |
| `NoContextError` | 2 | No `ScopedContext` active |
| `PrincipalNotFoundError` | 2 | Principal not found |
| `AccessDeniedError` | 3 | Principal lacks permission |
| `IsolationViolationError` | 3 | Operation breaches isolation |
| `ScopeNotFoundError` | 4 | Scope not found |
| `ScopeFrozenError` | 4 | Scope is frozen or archived |
| `TraceIntegrityError` | 6 | Hash chain broken |
| `RollbackDeniedError` | 7 | Rollback blocked by constraints |
| `RollbackFailedError` | 7 | Rollback execution failed |
| `EnvironmentNotFoundError` | 8 | Environment not found |
| `EnvironmentStateError` | 8 | Invalid state transition |
| `FlowError` | 9 | General flow error |
| `FlowBlockedError` | 9 | No channel permits the flow |
| `StageTransitionDeniedError` | 9 | Stage transition blocked |
| `PromotionDeniedError` | 9 | Promotion blocked |
| `DeploymentError` | 10 | General deployment error |
| `DeploymentGateFailedError` | 10 | Gate check failed |
| `SecretNotFoundError` | 11 | Secret not found |
| `SecretAccessDeniedError` | 11 | Ref access denied |
| `SecretRefExpiredError` | 11 | Ref expired |
| `IntegrationError` | 12 | Integration error |
| `ConnectorError` | 13 | Connector error |
| `ConnectorNotApprovedError` | 13 | Connector not approved |
| `ConnectorPolicyViolation` | 13 | Traffic violates policy |
| `ConnectorRevokedError` | 13 | Connector revoked |
| `ContractNotFoundError` | A2 | Contract not found |
| `ContractValidationError` | A2 | Validation failed |
| `TemplateNotFoundError` | A7 | Template not found |
| `TemplateVersionNotFoundError` | A7 | Template version not found |
| `TemplateInstantiationError` | A7 | Instantiation failed |
