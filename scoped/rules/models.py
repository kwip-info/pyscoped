"""Data models for Layer 5: Rule Engine."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from scoped.types import Lifecycle


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RuleType(Enum):
    """Categories of rules."""
    ACCESS = "access"
    SHARING = "sharing"
    VISIBILITY = "visibility"
    OWNERSHIP = "ownership"
    CONSTRAINT = "constraint"
    REDACTION = "redaction"
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota"
    FEATURE_FLAG = "feature_flag"


class RuleEffect(Enum):
    """The outcome when a rule matches."""
    ALLOW = "ALLOW"
    DENY = "DENY"


class BindingTargetType(Enum):
    """What a rule can be bound to."""
    SCOPE = "scope"
    PRINCIPAL = "principal"
    OBJECT_TYPE = "object_type"
    OBJECT = "object"
    ENVIRONMENT = "environment"
    CONNECTOR = "connector"


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Rule:
    """A condition-effect pair: "if this condition is true, apply this effect."

    Conditions are stored as a JSON dict with matcher fields:
      - action: list of ActionType values to match
      - principal_kind: list of principal kinds to match
      - object_type: list of object types to match
      - scope_id: list of scope IDs to match
      - custom: arbitrary key-value predicates
    """
    id: str
    name: str
    rule_type: RuleType
    effect: RuleEffect
    priority: int
    conditions: dict[str, Any]
    created_at: datetime
    created_by: str
    current_version: int = 1
    description: str = ""
    registry_entry_id: str | None = None
    lifecycle: Lifecycle = Lifecycle.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    @property
    def typed_conditions(self) -> Any:
        """Parse conditions into a typed model based on rule_type.

        Returns a ``RuleConditions`` instance (Pydantic model) for
        validated access.  Falls back to the raw dict on parse failure.
        """
        from scoped.rules.conditions import parse_conditions

        try:
            return parse_conditions(self.conditions, self.rule_type)
        except Exception:
            return self.conditions

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "rule_type": self.rule_type.value,
            "effect": self.effect.value,
            "priority": self.priority,
            "conditions": self.conditions,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "current_version": self.current_version,
            "lifecycle": self.lifecycle.name,
        }


@dataclass(frozen=True, slots=True)
class RuleVersion:
    """Immutable snapshot of a rule at a version."""
    id: str
    rule_id: str
    version: int
    conditions: dict[str, Any]
    effect: RuleEffect
    priority: int
    created_at: datetime
    created_by: str
    change_reason: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "version": self.version,
            "conditions": self.conditions,
            "effect": self.effect.value,
            "priority": self.priority,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "change_reason": self.change_reason,
        }


@dataclass(frozen=True, slots=True)
class RuleBinding:
    """Attaches a rule to a target (scope, principal, object_type, etc.)."""
    id: str
    rule_id: str
    target_type: BindingTargetType
    target_id: str
    bound_at: datetime
    bound_by: str
    lifecycle: Lifecycle = Lifecycle.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "target_type": self.target_type.value,
            "target_id": self.target_id,
            "bound_at": self.bound_at.isoformat(),
            "bound_by": self.bound_by,
            "lifecycle": self.lifecycle.name,
        }


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Result of evaluating rules for an access request."""
    allowed: bool
    matching_rules: tuple[Rule, ...]
    deny_rules: tuple[Rule, ...]
    allow_rules: tuple[Rule, ...]

    def __bool__(self) -> bool:
        return self.allowed

    def __repr__(self) -> str:
        return (
            f"EvaluationResult(allowed={self.allowed}, "
            f"matched={len(self.matching_rules)}, "
            f"denies={len(self.deny_rules)}, "
            f"allows={len(self.allow_rules)})"
        )


# ---------------------------------------------------------------------------
# Row mapping helpers
# ---------------------------------------------------------------------------

def rule_from_row(row: dict[str, Any]) -> Rule:
    return Rule(
        id=row["id"],
        name=row["name"],
        description=row.get("description", ""),
        rule_type=RuleType(row["rule_type"]),
        effect=RuleEffect(row["effect"]),
        priority=row["priority"],
        conditions=json.loads(row["conditions_json"]),
        registry_entry_id=row.get("registry_entry_id"),
        created_at=datetime.fromisoformat(row["created_at"]),
        created_by=row["created_by"],
        lifecycle=Lifecycle[row["lifecycle"]],
        current_version=row["current_version"],
    )


def rule_version_from_row(row: dict[str, Any]) -> RuleVersion:
    return RuleVersion(
        id=row["id"],
        rule_id=row["rule_id"],
        version=row["version"],
        conditions=json.loads(row["conditions_json"]),
        effect=RuleEffect(row["effect"]),
        priority=row["priority"],
        created_at=datetime.fromisoformat(row["created_at"]),
        created_by=row["created_by"],
        change_reason=row.get("change_reason", ""),
    )


def binding_from_row(row: dict[str, Any]) -> RuleBinding:
    return RuleBinding(
        id=row["id"],
        rule_id=row["rule_id"],
        target_type=BindingTargetType(row["target_type"]),
        target_id=row["target_id"],
        bound_at=datetime.fromisoformat(row["bound_at"]),
        bound_by=row["bound_by"],
        lifecycle=Lifecycle[row["lifecycle"]],
    )
