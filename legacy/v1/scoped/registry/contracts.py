"""Contracts & Schema Validation.

A Contract declares the shape of an object type — its fields, types,
required constraints, and cross-field validations. Contracts are versioned,
registered constructs that feed into connector schema negotiation,
deployment gate checks, and runtime object validation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import sqlalchemy as sa

from scoped.exceptions import (
    ContractNotFoundError,
    ContractValidationError,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import contract_versions, contracts
from scoped.types import Lifecycle, generate_id, now_utc


# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------

class FieldType(Enum):
    """Supported field types in a contract."""

    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    JSON = "json"
    LIST = "list"
    REF = "ref"           # reference to another object
    BLOB = "blob"         # binary content reference
    ANY = "any"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ContractField:
    """A single field in a contract schema."""

    name: str
    field_type: FieldType
    required: bool = True
    default: Any = None
    description: str = ""
    constraints: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "field_type": self.field_type.value,
            "required": self.required,
            "default": self.default,
            "description": self.description,
            "constraints": self.constraints,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContractField:
        return cls(
            name=data["name"],
            field_type=FieldType(data["field_type"]),
            required=data.get("required", True),
            default=data.get("default"),
            description=data.get("description", ""),
            constraints=data.get("constraints", {}),
        )


@dataclass(frozen=True, slots=True)
class ContractConstraint:
    """A cross-field or whole-object constraint."""

    name: str
    constraint_type: str    # "unique_together", "at_least_one", "depends_on", "custom"
    config: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "constraint_type": self.constraint_type,
            "config": self.config,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContractConstraint:
        return cls(
            name=data["name"],
            constraint_type=data["constraint_type"],
            config=data.get("config", {}),
            description=data.get("description", ""),
        )


@dataclass(slots=True)
class Contract:
    """A schema contract for an object type."""

    id: str
    name: str
    object_type: str
    owner_id: str
    created_at: datetime
    description: str = ""
    current_version: int = 1
    lifecycle: Lifecycle = Lifecycle.ACTIVE
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "object_type": self.object_type,
            "owner_id": self.owner_id,
            "current_version": self.current_version,
            "lifecycle": self.lifecycle.name,
            "created_at": self.created_at.isoformat(),
            "description": self.description,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class ContractVersion:
    """An immutable version of a contract's field definitions."""

    id: str
    contract_id: str
    version: int
    fields: tuple[ContractField, ...]
    created_at: datetime
    created_by: str
    constraints: tuple[ContractConstraint, ...] = ()
    change_reason: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "contract_id": self.contract_id,
            "version": self.version,
            "fields": [f.snapshot() for f in self.fields],
            "constraints": [c.snapshot() for c in self.constraints],
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "change_reason": self.change_reason,
        }

    @property
    def field_names(self) -> frozenset[str]:
        return frozenset(f.name for f in self.fields)

    @property
    def required_fields(self) -> frozenset[str]:
        return frozenset(f.name for f in self.fields if f.required)

    def get_field(self, name: str) -> ContractField | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None


# ---------------------------------------------------------------------------
# Row mapping
# ---------------------------------------------------------------------------

def contract_from_row(row: dict[str, Any]) -> Contract:
    return Contract(
        id=row["id"],
        name=row["name"],
        object_type=row["object_type"],
        owner_id=row["owner_id"],
        current_version=row.get("current_version", 1),
        created_at=datetime.fromisoformat(row["created_at"]),
        description=row.get("description", ""),
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
        metadata=json.loads(row.get("metadata_json", "{}")),
    )


def contract_version_from_row(row: dict[str, Any]) -> ContractVersion:
    fields_data = json.loads(row.get("fields_json", "[]"))
    constraints_data = json.loads(row.get("constraints_json", "[]"))
    return ContractVersion(
        id=row["id"],
        contract_id=row["contract_id"],
        version=row["version"],
        fields=tuple(ContractField.from_dict(f) for f in fields_data),
        constraints=tuple(ContractConstraint.from_dict(c) for c in constraints_data),
        created_at=datetime.fromisoformat(row["created_at"]),
        created_by=row["created_by"],
        change_reason=row.get("change_reason", ""),
    )


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Result of validating data against a contract."""

    valid: bool
    errors: tuple[str, ...] = ()

    def raise_if_invalid(self) -> None:
        if not self.valid:
            raise ContractValidationError(
                f"Validation failed: {'; '.join(self.errors)}",
                context={"errors": list(self.errors)},
            )


# ---------------------------------------------------------------------------
# Contract store (CRUD)
# ---------------------------------------------------------------------------

class ContractStore:
    """Manages contract lifecycle and persistence."""

    def __init__(self, backend: Any, *, audit_writer: Any = None) -> None:
        self._backend = backend
        self._audit = audit_writer

    def create_contract(
        self,
        *,
        name: str,
        object_type: str,
        owner_id: str,
        fields: list[ContractField],
        description: str = "",
        constraints: list[ContractConstraint] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Contract:
        """Create a new contract with an initial version."""
        contract_id = generate_id()
        ts = now_utc()

        contract = Contract(
            id=contract_id,
            name=name,
            object_type=object_type,
            owner_id=owner_id,
            created_at=ts,
            description=description,
            metadata=metadata or {},
        )

        stmt = sa.insert(contracts).values(
            id=contract.id,
            name=contract.name,
            description=contract.description,
            object_type=contract.object_type,
            owner_id=contract.owner_id,
            current_version=contract.current_version,
            created_at=ts.isoformat(),
            lifecycle=contract.lifecycle.name,
            metadata_json=json.dumps(contract.metadata),
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        # Create the initial version
        version_id = generate_id()
        fields_json = json.dumps([f.snapshot() for f in fields])
        constraints_json = json.dumps(
            [c.snapshot() for c in (constraints or [])]
        )
        stmt = sa.insert(contract_versions).values(
            id=version_id,
            contract_id=contract_id,
            version=1,
            fields_json=fields_json,
            constraints_json=constraints_json,
            created_at=ts.isoformat(),
            created_by=owner_id,
            change_reason="Initial version",
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        return contract

    def get_contract(self, contract_id: str) -> Contract | None:
        stmt = sa.select(contracts).where(contracts.c.id == contract_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return contract_from_row(row) if row else None

    def get_contract_or_raise(self, contract_id: str) -> Contract:
        contract = self.get_contract(contract_id)
        if contract is None:
            raise ContractNotFoundError(
                f"Contract {contract_id} not found",
                context={"contract_id": contract_id},
            )
        return contract

    def get_contract_for_type(self, object_type: str) -> Contract | None:
        """Get the active contract for an object type."""
        stmt = (
            sa.select(contracts)
            .where(contracts.c.object_type == object_type)
            .where(contracts.c.lifecycle == "ACTIVE")
            .order_by(contracts.c.created_at.desc())
            .limit(1)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return contract_from_row(row) if row else None

    def list_contracts(
        self,
        *,
        owner_id: str | None = None,
        object_type: str | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[Contract]:
        stmt = sa.select(contracts)

        if owner_id:
            stmt = stmt.where(contracts.c.owner_id == owner_id)
        if object_type:
            stmt = stmt.where(contracts.c.object_type == object_type)
        if active_only:
            stmt = stmt.where(contracts.c.lifecycle == "ACTIVE")

        stmt = stmt.order_by(contracts.c.created_at.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [contract_from_row(r) for r in rows]

    def get_version(self, contract_id: str, version: int | None = None) -> ContractVersion | None:
        """Get a specific version, or the latest if version is None."""
        if version is not None:
            stmt = (
                sa.select(contract_versions)
                .where(contract_versions.c.contract_id == contract_id)
                .where(contract_versions.c.version == version)
            )
        else:
            stmt = (
                sa.select(contract_versions)
                .where(contract_versions.c.contract_id == contract_id)
                .order_by(contract_versions.c.version.desc())
                .limit(1)
            )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return contract_version_from_row(row) if row else None

    def get_all_versions(self, contract_id: str) -> list[ContractVersion]:
        stmt = (
            sa.select(contract_versions)
            .where(contract_versions.c.contract_id == contract_id)
            .order_by(contract_versions.c.version)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [contract_version_from_row(r) for r in rows]

    def update_contract(
        self,
        contract_id: str,
        *,
        fields: list[ContractField],
        actor_id: str,
        constraints: list[ContractConstraint] | None = None,
        change_reason: str = "",
    ) -> ContractVersion:
        """Create a new version of a contract."""
        contract = self.get_contract_or_raise(contract_id)
        new_version = contract.current_version + 1
        ts = now_utc()

        version_id = generate_id()
        fields_json = json.dumps([f.snapshot() for f in fields])
        constraints_json = json.dumps(
            [c.snapshot() for c in (constraints or [])]
        )

        stmt = sa.insert(contract_versions).values(
            id=version_id,
            contract_id=contract_id,
            version=new_version,
            fields_json=fields_json,
            constraints_json=constraints_json,
            created_at=ts.isoformat(),
            created_by=actor_id,
            change_reason=change_reason,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        stmt = (
            sa.update(contracts)
            .where(contracts.c.id == contract_id)
            .values(current_version=new_version)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        return ContractVersion(
            id=version_id,
            contract_id=contract_id,
            version=new_version,
            fields=tuple(fields),
            constraints=tuple(constraints or []),
            created_at=ts,
            created_by=actor_id,
            change_reason=change_reason,
        )

    def deprecate(self, contract_id: str) -> Contract:
        contract = self.get_contract_or_raise(contract_id)
        stmt = (
            sa.update(contracts)
            .where(contracts.c.id == contract_id)
            .values(lifecycle=Lifecycle.DEPRECATED.name)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
        contract.lifecycle = Lifecycle.DEPRECATED
        return contract

    def archive(self, contract_id: str) -> Contract:
        contract = self.get_contract_or_raise(contract_id)
        stmt = (
            sa.update(contracts)
            .where(contracts.c.id == contract_id)
            .values(lifecycle=Lifecycle.ARCHIVED.name)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
        contract.lifecycle = Lifecycle.ARCHIVED
        return contract

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(
        self,
        data: dict[str, Any],
        contract_id: str,
        *,
        version: int | None = None,
    ) -> ValidationResult:
        """Validate data against a contract version."""
        cv = self.get_version(contract_id, version)
        if cv is None:
            raise ContractNotFoundError(
                f"Contract version not found",
                context={"contract_id": contract_id, "version": version},
            )
        return validate_against_version(data, cv)

    def validate_for_type(
        self,
        data: dict[str, Any],
        object_type: str,
    ) -> ValidationResult:
        """Validate data against the active contract for an object type."""
        contract = self.get_contract_for_type(object_type)
        if contract is None:
            # No contract defined — validation passes (no constraints)
            return ValidationResult(valid=True)
        return self.validate(data, contract.id)


# ---------------------------------------------------------------------------
# Pure validation logic
# ---------------------------------------------------------------------------

_TYPE_VALIDATORS: dict[FieldType, type | tuple[type, ...]] = {
    FieldType.STRING: str,
    FieldType.INTEGER: int,
    FieldType.FLOAT: (int, float),
    FieldType.BOOLEAN: bool,
    FieldType.DATETIME: str,     # ISO format string
    FieldType.JSON: (dict, list),
    FieldType.LIST: list,
    FieldType.REF: str,
    FieldType.BLOB: str,
    FieldType.ANY: object,
}


def validate_against_version(
    data: dict[str, Any],
    contract_version: ContractVersion,
) -> ValidationResult:
    """Validate a data dict against a contract version."""
    errors: list[str] = []

    # Check required fields
    for f in contract_version.fields:
        if f.required and f.name not in data and f.default is None:
            errors.append(f"Missing required field: {f.name}")

    # Check field types and constraints
    for f in contract_version.fields:
        if f.name not in data:
            continue

        value = data[f.name]

        # Type check
        expected = _TYPE_VALIDATORS.get(f.field_type, object)
        if not isinstance(value, expected):
            errors.append(
                f"Field '{f.name}': expected {f.field_type.value}, "
                f"got {type(value).__name__}"
            )
            continue

        # Field-level constraints
        if "min_length" in f.constraints and isinstance(value, str):
            if len(value) < f.constraints["min_length"]:
                errors.append(
                    f"Field '{f.name}': length {len(value)} < "
                    f"min_length {f.constraints['min_length']}"
                )

        if "max_length" in f.constraints and isinstance(value, str):
            if len(value) > f.constraints["max_length"]:
                errors.append(
                    f"Field '{f.name}': length {len(value)} > "
                    f"max_length {f.constraints['max_length']}"
                )

        if "min_value" in f.constraints and isinstance(value, (int, float)):
            if value < f.constraints["min_value"]:
                errors.append(
                    f"Field '{f.name}': {value} < min_value {f.constraints['min_value']}"
                )

        if "max_value" in f.constraints and isinstance(value, (int, float)):
            if value > f.constraints["max_value"]:
                errors.append(
                    f"Field '{f.name}': {value} > max_value {f.constraints['max_value']}"
                )

        if "pattern" in f.constraints and isinstance(value, str):
            import re
            if not re.match(f.constraints["pattern"], value):
                errors.append(
                    f"Field '{f.name}': does not match pattern '{f.constraints['pattern']}'"
                )

        if "choices" in f.constraints:
            if value not in f.constraints["choices"]:
                errors.append(
                    f"Field '{f.name}': {value!r} not in allowed choices "
                    f"{f.constraints['choices']}"
                )

    # Check for unknown fields
    known_fields = contract_version.field_names
    for key in data:
        if key not in known_fields:
            errors.append(f"Unknown field: {key}")

    # Cross-field constraints
    for constraint in contract_version.constraints:
        _errors = _validate_constraint(data, constraint, contract_version)
        errors.extend(_errors)

    return ValidationResult(valid=len(errors) == 0, errors=tuple(errors))


def _validate_constraint(
    data: dict[str, Any],
    constraint: ContractConstraint,
    contract_version: ContractVersion,
) -> list[str]:
    """Evaluate a single cross-field constraint."""
    errors: list[str] = []
    ct = constraint.constraint_type

    if ct == "at_least_one":
        fields = constraint.config.get("fields", [])
        if not any(data.get(f) for f in fields):
            errors.append(
                f"Constraint '{constraint.name}': at least one of "
                f"{fields} must be provided"
            )

    elif ct == "depends_on":
        field_name = constraint.config.get("field", "")
        depends_on = constraint.config.get("depends_on", "")
        if field_name in data and depends_on not in data:
            errors.append(
                f"Constraint '{constraint.name}': field '{field_name}' "
                f"requires '{depends_on}' to be present"
            )

    elif ct == "mutually_exclusive":
        fields = constraint.config.get("fields", [])
        present = [f for f in fields if f in data]
        if len(present) > 1:
            errors.append(
                f"Constraint '{constraint.name}': fields {present} "
                f"are mutually exclusive"
            )

    return errors


# ---------------------------------------------------------------------------
# Contract diff
# ---------------------------------------------------------------------------

def diff_contracts(
    old: ContractVersion,
    new: ContractVersion,
) -> dict[str, Any]:
    """Compare two contract versions and return differences."""
    old_fields = {f.name: f for f in old.fields}
    new_fields = {f.name: f for f in new.fields}

    added = [f.name for f in new.fields if f.name not in old_fields]
    removed = [f.name for f in old.fields if f.name not in new_fields]
    modified = []

    for name in old_fields:
        if name in new_fields:
            if old_fields[name].snapshot() != new_fields[name].snapshot():
                modified.append(name)

    return {
        "added_fields": added,
        "removed_fields": removed,
        "modified_fields": modified,
        "old_version": old.version,
        "new_version": new.version,
    }
