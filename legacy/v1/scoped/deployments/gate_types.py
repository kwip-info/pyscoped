"""Typed deployment gate detail models.

Each ``GateType`` has a corresponding Pydantic model for its ``details``
dict.  Follows the same pattern as ``scoped.rules.conditions``.

Usage::

    from scoped.deployments.gate_types import StageCheckDetails, gate_details_to_dict

    details = StageCheckDetails(required_stage="review", current_stage="draft")
    raw = gate_details_to_dict(details)
"""

from __future__ import annotations

from typing import Any, Union

from pydantic import BaseModel, ConfigDict

from scoped.deployments.models import GateType


class StageCheckDetails(BaseModel):
    """Details for a stage-check gate."""
    model_config = ConfigDict(frozen=True)

    required_stage: str
    current_stage: str
    passed: bool = False


class RuleCheckDetails(BaseModel):
    """Details for a rule-check gate."""
    model_config = ConfigDict(frozen=True)

    rule_ids: list[str] = []
    evaluation_result: str | None = None


class ApprovalDetails(BaseModel):
    """Details for an approval gate."""
    model_config = ConfigDict(frozen=True)

    approver_id: str | None = None
    approved_at: str | None = None
    comment: str = ""


class CustomGateDetails(BaseModel):
    """Details for a custom gate (pass-through)."""
    model_config = ConfigDict(frozen=True, extra="allow")


GateDetails = Union[StageCheckDetails, RuleCheckDetails, ApprovalDetails, CustomGateDetails]

_GATE_DETAIL_MODELS: dict[GateType, type[BaseModel]] = {
    GateType.STAGE_CHECK: StageCheckDetails,
    GateType.RULE_CHECK: RuleCheckDetails,
    GateType.APPROVAL: ApprovalDetails,
    GateType.CUSTOM: CustomGateDetails,
}


def parse_gate_details(raw: dict[str, Any], gate_type: GateType) -> GateDetails:
    """Parse a raw details dict into the typed model for the given gate type."""
    model_cls = _GATE_DETAIL_MODELS.get(gate_type, CustomGateDetails)
    return model_cls.model_validate(raw)


def gate_details_to_dict(details: GateDetails | dict[str, Any]) -> dict[str, Any]:
    """Serialize gate details to a plain dict for JSON storage.

    If already a dict, returns it unchanged (backward compatibility).
    """
    if isinstance(details, dict):
        return details
    return details.model_dump(mode="json", exclude_none=True)
