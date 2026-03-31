"""Redaction engine — field-level data masking based on viewer context.

Redaction rules declare which fields of an object type should be masked,
replaced, or omitted when the viewer matches certain conditions (principal
kind, scope, custom attributes).

A redaction rule's ``conditions`` dict extends the standard rule condition
format with a ``redactions`` key:

    {
        "object_type": "CreditCard",
        "principal_kind": ["viewer", "auditor"],
        "redactions": {
            "card_number": {"strategy": "mask", "mask_char": "*", "visible_chars": 4},
            "cvv": {"strategy": "omit"},
            "holder_name": {"strategy": "replace", "replacement": "[REDACTED]"},
        }
    }

Strategies:
  - ``mask``    — replace characters with *mask_char*, optionally keeping
                  the last *visible_chars*.
  - ``replace`` — swap the entire value with a fixed *replacement* string.
  - ``omit``    — remove the field from the output entirely.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import Enum
from typing import Any

from scoped.rules.models import Rule, RuleEffect, RuleType


class RedactionStrategy(Enum):
    """How a field value should be redacted."""
    MASK = "mask"
    REPLACE = "replace"
    OMIT = "omit"


@dataclass(frozen=True, slots=True)
class FieldRedaction:
    """Describes how a single field should be redacted."""
    field_name: str
    strategy: RedactionStrategy
    mask_char: str = "*"
    visible_chars: int = 0
    replacement: str = "[REDACTED]"

    @classmethod
    def from_dict(cls, field_name: str, config: dict[str, Any]) -> FieldRedaction:
        return cls(
            field_name=field_name,
            strategy=RedactionStrategy(config.get("strategy", "replace")),
            mask_char=config.get("mask_char", "*"),
            visible_chars=config.get("visible_chars", 0),
            replacement=config.get("replacement", "[REDACTED]"),
        )


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """Outcome of applying redactions to a data dict."""
    data: dict[str, Any]
    redacted_fields: tuple[str, ...]
    rules_applied: tuple[str, ...]


class RedactionEngine:
    """Apply field-level redaction to object data based on matching rules.

    Usage::

        engine = RedactionEngine(rules)
        result = engine.apply(
            data={"card_number": "4111111111111111", "cvv": "123"},
            object_type="CreditCard",
            principal_kind="viewer",
            scope_id="scope-1",
        )
        # result.data == {"card_number": "************1111"}
        # result.redacted_fields == ("card_number", "cvv")
    """

    def __init__(self, rules: list[Rule]) -> None:
        self._rules = [
            r for r in rules
            if r.rule_type == RuleType.REDACTION and r.is_active
        ]

    def apply(
        self,
        data: dict[str, Any],
        *,
        object_type: str | None = None,
        principal_kind: str | None = None,
        scope_id: str | None = None,
    ) -> RedactionResult:
        """Apply all matching redaction rules to *data* and return the result."""
        result = copy.deepcopy(data)
        redacted: list[str] = []
        applied_rule_ids: list[str] = []

        for rule in self._rules:
            if not self._matches(rule, object_type=object_type,
                                 principal_kind=principal_kind, scope_id=scope_id):
                continue

            redactions_cfg = rule.conditions.get("redactions", {})
            if not redactions_cfg:
                continue

            applied_rule_ids.append(rule.id)

            for field_name, config in redactions_cfg.items():
                fr = FieldRedaction.from_dict(field_name, config)
                if fr.strategy == RedactionStrategy.OMIT:
                    if field_name in result:
                        del result[field_name]
                        if field_name not in redacted:
                            redacted.append(field_name)
                elif field_name in result:
                    result[field_name] = self._redact_value(
                        result[field_name], fr,
                    )
                    if field_name not in redacted:
                        redacted.append(field_name)

        return RedactionResult(
            data=result,
            redacted_fields=tuple(redacted),
            rules_applied=tuple(applied_rule_ids),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _matches(
        rule: Rule,
        *,
        object_type: str | None,
        principal_kind: str | None,
        scope_id: str | None,
    ) -> bool:
        """Check if a redaction rule matches the current context."""
        conds = rule.conditions

        if "object_type" in conds and object_type is not None:
            allowed = conds["object_type"]
            if isinstance(allowed, list):
                if object_type not in allowed:
                    return False
            elif object_type != allowed:
                return False

        if "principal_kind" in conds and principal_kind is not None:
            allowed = conds["principal_kind"]
            if isinstance(allowed, list):
                if principal_kind not in allowed:
                    return False
            elif principal_kind != allowed:
                return False

        if "scope_id" in conds and scope_id is not None:
            allowed = conds["scope_id"]
            if isinstance(allowed, list):
                if scope_id not in allowed:
                    return False
            elif scope_id != allowed:
                return False

        return True

    @staticmethod
    def _redact_value(value: Any, fr: FieldRedaction) -> Any:
        """Apply a single field redaction to a value."""
        if fr.strategy == RedactionStrategy.REPLACE:
            return fr.replacement

        if fr.strategy == RedactionStrategy.MASK:
            s = str(value)
            if fr.visible_chars > 0 and len(s) > fr.visible_chars:
                masked = fr.mask_char * (len(s) - fr.visible_chars)
                return masked + s[-fr.visible_chars:]
            return fr.mask_char * len(s)

        return value  # pragma: no cover — OMIT handled earlier
