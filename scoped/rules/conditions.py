"""Typed rule conditions — validated at creation time.

Each rule type has a corresponding Pydantic model that validates the
condition structure.  Conditions are stored as JSON in the database
and parsed back into typed models on access.

Usage::

    from scoped.rules.conditions import AccessCondition, parse_conditions

    # Typed creation (validated immediately)
    cond = AccessCondition(action=["create", "read"], object_type="invoice")

    # From raw dict (validated on parse)
    cond = parse_conditions({"action": "create"}, RuleType.ACCESS)

    # Serialize for storage
    raw = conditions_to_dict(cond)  # -> {"action": "create"}
"""

from __future__ import annotations

from typing import Any, Union

from pydantic import BaseModel, ConfigDict

from scoped.rules.models import RuleType


# ---------------------------------------------------------------------------
# Spec models (nested structures)
# ---------------------------------------------------------------------------


class RateLimitSpec(BaseModel):
    """Rate-limit parameters."""
    model_config = ConfigDict(frozen=True)

    max_count: int
    window_seconds: int


class QuotaSpec(BaseModel):
    """Quota enforcement parameters."""
    model_config = ConfigDict(frozen=True)

    max_count: int
    count_table: str = "scoped_objects"
    count_column: str = "object_type"
    count_value: str | None = None
    scope_column: str | None = None


class RedactionSpec(BaseModel):
    """How to redact a single field."""
    model_config = ConfigDict(frozen=True)

    strategy: str  # "mask", "replace", "omit"
    mask_char: str = "*"
    visible_chars: int = 0
    replacement: str = "[REDACTED]"


class FeatureFlagSpec(BaseModel):
    """Feature flag parameters."""
    model_config = ConfigDict(frozen=True)

    feature_name: str
    enabled: bool = True
    rollout_percentage: int = 100


# ---------------------------------------------------------------------------
# Condition models (one per RuleType)
# ---------------------------------------------------------------------------


class AccessCondition(BaseModel):
    """Condition for ACCESS, SHARING, VISIBILITY, OWNERSHIP, CONSTRAINT rules."""
    model_config = ConfigDict(frozen=True)

    action: str | list[str] | None = None
    principal_kind: str | list[str] | None = None
    object_type: str | list[str] | None = None
    scope_id: str | list[str] | None = None
    role: str | None = None


class RateLimitCondition(BaseModel):
    """Condition for RATE_LIMIT rules."""
    model_config = ConfigDict(frozen=True)

    action: str | list[str] | None = None
    rate_limit: RateLimitSpec


class QuotaCondition(BaseModel):
    """Condition for QUOTA rules."""
    model_config = ConfigDict(frozen=True)

    object_type: str | list[str] | None = None
    quota: QuotaSpec


class RedactionCondition(BaseModel):
    """Condition for REDACTION rules."""
    model_config = ConfigDict(frozen=True)

    object_type: str | None = None
    principal_kind: str | list[str] | None = None
    redactions: dict[str, RedactionSpec]


class FeatureFlagCondition(BaseModel):
    """Condition for FEATURE_FLAG rules."""
    model_config = ConfigDict(frozen=True)

    feature_flag: FeatureFlagSpec
    scope_id: str | list[str] | None = None


# Union of all condition types
RuleConditions = Union[
    AccessCondition,
    RateLimitCondition,
    QuotaCondition,
    RedactionCondition,
    FeatureFlagCondition,
]

# Map RuleType → condition model
_CONDITION_MODELS: dict[RuleType, type[BaseModel]] = {
    RuleType.ACCESS: AccessCondition,
    RuleType.SHARING: AccessCondition,
    RuleType.VISIBILITY: AccessCondition,
    RuleType.OWNERSHIP: AccessCondition,
    RuleType.CONSTRAINT: AccessCondition,
    RuleType.RATE_LIMIT: RateLimitCondition,
    RuleType.QUOTA: QuotaCondition,
    RuleType.REDACTION: RedactionCondition,
    RuleType.FEATURE_FLAG: FeatureFlagCondition,
}


# ---------------------------------------------------------------------------
# Parse / serialize
# ---------------------------------------------------------------------------


def parse_conditions(
    raw: dict[str, Any],
    rule_type: RuleType,
) -> RuleConditions:
    """Parse a raw conditions dict into a typed model.

    Validates the structure against the expected schema for the given
    ``rule_type``.  Raises ``pydantic.ValidationError`` if invalid.

    Unknown keys are silently ignored (forward compatibility).
    """
    model_cls = _CONDITION_MODELS.get(rule_type, AccessCondition)
    return model_cls.model_validate(raw)


def conditions_to_dict(conditions: RuleConditions | dict[str, Any]) -> dict[str, Any]:
    """Serialize conditions to a plain dict for JSON storage.

    If already a dict, returns it unchanged (backward compatibility).
    """
    if isinstance(conditions, dict):
        return conditions
    return conditions.model_dump(mode="json", exclude_none=True)
