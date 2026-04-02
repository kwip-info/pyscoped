"""Tests for 5E: Redaction performance — shallow copy optimization."""

import pytest
from scoped.rules.models import Rule, RuleEffect, RuleType
from scoped.rules.redaction import RedactionEngine
from scoped.types import Lifecycle, now_utc


def _make_redaction_rule(redactions: dict, rule_id: str = "r1") -> Rule:
    return Rule(
        id=rule_id,
        name="test-redaction",
        description="",
        rule_type=RuleType.REDACTION,
        effect=RuleEffect.DENY,
        priority=0,
        conditions={"redactions": redactions},
        created_at=now_utc(),
        created_by="system",
    )


class TestRedactionShallowCopy:
    """Verify shallow copy doesn't corrupt original data."""

    def test_original_dict_unmodified_after_mask(self):
        rule = _make_redaction_rule({
            "card_number": {"strategy": "mask", "visible_chars": 4},
        })
        engine = RedactionEngine([rule])
        original = {"card_number": "4111111111111111", "name": "Alice"}

        result = engine.apply(original)

        # Original must be untouched
        assert original["card_number"] == "4111111111111111"
        assert original["name"] == "Alice"
        # Result is redacted
        assert result.data["card_number"] == "************1111"
        assert result.data["name"] == "Alice"

    def test_original_dict_unmodified_after_omit(self):
        rule = _make_redaction_rule({
            "cvv": {"strategy": "omit"},
        })
        engine = RedactionEngine([rule])
        original = {"cvv": "123", "name": "Alice"}

        result = engine.apply(original)

        assert "cvv" in original  # original still has it
        assert "cvv" not in result.data  # result omits it

    def test_original_dict_unmodified_after_replace(self):
        rule = _make_redaction_rule({
            "name": {"strategy": "replace", "replacement": "[HIDDEN]"},
        })
        engine = RedactionEngine([rule])
        original = {"name": "Alice", "id": "1"}

        result = engine.apply(original)

        assert original["name"] == "Alice"
        assert result.data["name"] == "[HIDDEN]"

    def test_nested_data_preserved(self):
        """Shallow copy means nested objects are shared (by design).

        Redaction only assigns new values to top-level keys, so nested
        data is never mutated.
        """
        rule = _make_redaction_rule({
            "card_number": {"strategy": "mask", "visible_chars": 4},
        })
        engine = RedactionEngine([rule])
        nested = {"sub": {"deep": "value"}}
        original = {"card_number": "4111111111111111", "metadata": nested["sub"]}

        result = engine.apply(original)

        # Nested object is shared reference (shallow copy)
        assert result.data["metadata"] is original["metadata"]
        assert result.data["metadata"]["deep"] == "value"

    def test_no_matching_rules_returns_copy(self):
        engine = RedactionEngine([])
        original = {"x": 1}

        result = engine.apply(original)

        assert result.data == {"x": 1}
        assert result.data is not original  # Still a copy

    def test_redacted_fields_tracked(self):
        rule = _make_redaction_rule({
            "card_number": {"strategy": "mask"},
            "cvv": {"strategy": "omit"},
        })
        engine = RedactionEngine([rule])
        original = {"card_number": "4111", "cvv": "123", "name": "Alice"}

        result = engine.apply(original)

        assert "card_number" in result.redacted_fields
        assert "cvv" in result.redacted_fields
        assert "name" not in result.redacted_fields

    def test_rules_applied_tracked(self):
        rule = _make_redaction_rule(
            {"name": {"strategy": "replace"}}, rule_id="rule-abc",
        )
        engine = RedactionEngine([rule])
        result = engine.apply({"name": "Alice"})

        assert "rule-abc" in result.rules_applied
