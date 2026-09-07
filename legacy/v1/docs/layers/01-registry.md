# Layer 1: Universal Registry

## Purpose

The registry is the foundation of the entire framework. Its rule is absolute: **if it's not registered, it doesn't exist.**

Every construct that participates in Scoped — a data model, a function, a class, a relationship, a view, a secret, a connector, a plugin — must have a registry entry. This entry gives it a globally unique identity (URN), a lifecycle state, metadata, and discoverability.

The registry is what makes the compliance engine possible. If the framework can enumerate everything that *should* exist (by scanning the application) and compare it to what *does* exist (in the registry), it can detect gaps — unregistered constructs that bypass the framework's guarantees.

## Core Concepts

### URN (Universal Resource Name)

Every registered construct gets a URN:

```
scoped:<kind>:<namespace>:<name>:<version>
```

Examples:
- `scoped:MODEL:myapp:User:1`
- `scoped:FUNCTION:payments:process_charge:1`
- `scoped:CONNECTOR:acme:partner-bridge:3`

URNs are immutable identifiers. They never change once assigned. If a construct is versioned (its registry entry is updated), the version component increments.

**Validation:** URNs are validated at construction time. `kind`, `namespace`, and `name` must be non-empty strings, and `version` must be >= 1. Invalid URNs raise `ValueError` immediately — no invalid URN can enter the system.

### Registry Entry

A `RegistryEntry` contains:

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `urn` | The URN described above |
| `kind` | What type of construct this is (MODEL, FUNCTION, CLASS, SECRET, CONNECTOR, etc.) |
| `lifecycle` | Current state: `DRAFT → ACTIVE → DEPRECATED → ARCHIVED` |
| `registered_at` | When it was registered |
| `registered_by` | Which principal registered it |
| `target` | The actual Python object (class, function, etc.) — None for data instances |
| `metadata` | Arbitrary key-value data |
| `tags` | Set of string tags for categorization |
| `entry_version` | Version of the registry entry itself |

### Registry Kinds

Built-in kinds cover all framework constructs:

**Data/Schema:** MODEL, FIELD, RELATIONSHIP, INSTANCE
**Code:** FUNCTION, CLASS, METHOD, SIGNAL, TASK
**Behavioral:** VIEW, SERIALIZER, MIDDLEWARE
**Framework:** PRINCIPAL, SCOPE, RULE, ENVIRONMENT, PIPELINE, STAGE, FLOW_CHANNEL, DEPLOYMENT, SECRET, SECRET_REF, INTEGRATION, PLUGIN, PLUGIN_HOOK, CONNECTOR, MARKETPLACE_LISTING
**Extension:** CUSTOM (application-defined via `CustomKind`)

### Lifecycle

Every registered construct moves through a lifecycle:

```
DRAFT ──→ ACTIVE ──→ DEPRECATED ──→ ARCHIVED
```

- **DRAFT**: Registered but not yet active. Cannot be referenced by other constructs.
- **ACTIVE**: Live and operational. The normal state.
- **DEPRECATED**: Still functional but marked for removal. Triggers warnings.
- **ARCHIVED**: Soft-removed. Not operational, not visible in normal queries, but retained for audit and rollback.

Lifecycle transitions are traced actions (audit Layer 6) and can be governed by rules (Layer 5).

## How It Connects

### To Layer 2 (Identity)
Every principal is a registered construct. The registry doesn't know *what* a principal is — it just knows it exists, what kind it is, and who registered it. Identity defines the semantics; the registry provides the identity.

### To Layer 3 (Objects)
Every scoped object has a registry entry. The object's type (what model it is) is resolved through the registry. When a new object is created, it gets both a registry entry and an object record.

### To Layer 5 (Rules)
Rules are registered constructs. Rule bindings reference registry entries. The rule engine resolves targets through the registry.

### To Layer 6 (Audit)
Every registry mutation (register, lifecycle change, metadata update) produces a trace entry. The registry is both a *producer* of audit events and a *dependency* of the audit system (trace entries reference registry URNs).

### To Layer 11 (Secrets)
Secrets are registered constructs with the SECRET kind. Secret refs are registered with SECRET_REF kind. The registry provides the identity; the secrets layer provides the encryption.

### To Layer 12 (Integrations)
Plugins and integrations are registered constructs. Plugin hooks are registered. The registry is how the framework discovers what plugins provide and what hooks they've registered for.

### To Layer 0 (Compliance)
The compliance engine's most fundamental check is **registry completeness** — scanning the application to find every class, function, model, and view, then verifying each has a registry entry.

## Extensions

This layer has been extended with:

- **[A2: Contracts & Schema Validation](../extensions/A2-contracts.md)** — Declares the shape of object types via `Contract` and `ContractField`. Provides `ContractValidator` for runtime validation against declared schemas.
- **[A7: General Templates](../extensions/A7-templates.md)** — Reusable blueprints for any construct type via `Template` and `TemplateStore`. Instantiation merges overrides via deep merge.

## Files

```
scoped/registry/
    __init__.py          # Public API
    base.py              # RegistryEntry, Registry (thread-safe, multi-indexed)
    kinds.py             # RegistryKind enum + CustomKind extension (includes TEMPLATE kind)
    decorators.py        # @register, @track, register_instance()
    store.py             # RegistryStore interface + InMemoryRegistryStore
    sqlite_store.py      # SQLite-backed persistence
    introspection.py     # Module/package scanning for compliance
    contracts.py         # [A2] Contract, ContractStore, ContractValidator
    templates.py         # [A7] Template, TemplateStore, InstantiationResult
```

## Schema

```sql
CREATE TABLE registry_entries (
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
```

## Usage

```python
from scoped.registry import register, track, RegistryKind

# Explicit registration
@register(RegistryKind.CLASS, namespace="payments")
class PaymentProcessor:
    ...

# Inferred registration
@track
def process_charge(amount, currency):
    ...

# Runtime instance registration
from scoped.registry.decorators import register_instance
entry = register_instance(
    my_data_object,
    namespace="orders",
    name="order-12345",
    registered_by="user-abc",
)
```

## Invariants

1. Every construct in the application MUST have a registry entry.
2. URNs are globally unique and immutable.
3. Registry mutations are traced.
4. Archived entries are retained forever (for audit and rollback).
5. The registry can be frozen after application startup to prevent runtime registration of new construct types.
