# A2: Contracts & Schema Validation

**Extends:** Layer 1 (Registry) + Layer 3 (Objects)

## Purpose

The registry knows *that* something exists, but not *what shape* it takes. Contracts declare the schema of an object type — its fields, types, required constraints, and cross-field validations. Contracts feed into connector schema negotiation, deployment gate checks, and runtime object validation.

## Core Concepts

### Contract

A registered construct declaring the shape of an object type.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `name` | Human-readable label (e.g., "UserProfile") |
| `description` | What this contract describes |
| `object_type` | Which object type this contract governs |
| `owner_id` | The principal who created it |
| `current_version` | Latest version number |
| `lifecycle` | ACTIVE, ARCHIVED |

### ContractField

A single field declaration within a contract.

| Field | Purpose |
|-------|---------|
| `field_name` | Name of the field |
| `field_type` | Type: STRING, INTEGER, FLOAT, BOOLEAN, DATETIME, JSON, ARRAY, ENUM |
| `required` | Whether the field must be present |
| `constraints` | JSON — min/max, pattern, enum_values, min_length, max_length |

### ContractVersion

Contracts are versioned. Every update creates a new `ContractVersion` with the full field set, enabling schema evolution tracking.

### Validation

`ContractValidator.validate(data, contract)` checks:

1. Required fields are present
2. Field types match declared types
3. Constraints are satisfied (min/max, patterns, enums, lengths)
4. No undeclared fields (optional strict mode)

Returns a `ValidationResult` with per-field errors.

## Schema

```sql
CREATE TABLE contracts (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    object_type     TEXT NOT NULL,
    owner_id        TEXT NOT NULL REFERENCES principals(id),
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    current_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE contract_versions (
    id              TEXT PRIMARY KEY,
    contract_id     TEXT NOT NULL REFERENCES contracts(id),
    version         INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    change_reason   TEXT NOT NULL DEFAULT '',
    UNIQUE(contract_id, version)
);

CREATE TABLE contract_fields (
    id              TEXT PRIMARY KEY,
    contract_version_id TEXT NOT NULL REFERENCES contract_versions(id),
    field_name      TEXT NOT NULL,
    field_type      TEXT NOT NULL,
    required        INTEGER NOT NULL DEFAULT 0,
    constraints_json TEXT NOT NULL DEFAULT '{}'
);
```

## Files

```
scoped/registry/
    contracts.py       # Contract, ContractVersion, ContractField, FieldType,
                       # ContractStore, ContractValidator, ValidationResult
```

## Usage

```python
from scoped.registry.contracts import ContractStore, ContractValidator, FieldType

store = ContractStore(backend)

# Create a contract
contract = store.create_contract(
    name="UserProfile",
    object_type="user_profile",
    owner_id=principal_id,
    fields=[
        {"field_name": "email", "field_type": FieldType.STRING, "required": True,
         "constraints": {"pattern": r"^[^@]+@[^@]+\.[^@]+$"}},
        {"field_name": "age", "field_type": FieldType.INTEGER,
         "constraints": {"min_value": 0, "max_value": 200}},
    ],
)

# Validate data
validator = ContractValidator(backend)
result = validator.validate({"email": "user@example.com", "age": 25}, contract.id)
assert result.is_valid
```

## Invariants

1. Contracts are versioned — updates create new versions, old versions are retained.
2. Only the contract owner can update or archive a contract.
3. Validation is stateless — it checks data against the contract without side effects.
4. Field types cover all JSON-representable data.
