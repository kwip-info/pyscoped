"""Tests for the redaction engine — field-level data masking."""

import pytest

from scoped.identity.principal import PrincipalStore
from scoped.rules.engine import RuleStore
from scoped.rules.models import Rule, RuleEffect, RuleType
from scoped.rules.redaction import (
    FieldRedaction,
    RedactionEngine,
    RedactionResult,
    RedactionStrategy,
)


@pytest.fixture
def admin(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    return store.create_principal(kind="user", display_name="Admin", principal_id="admin")


@pytest.fixture
def rule_store(sqlite_backend):
    return RuleStore(sqlite_backend)


def _make_redaction_rule(
    rule_store,
    *,
    created_by: str,
    object_type: str = "CreditCard",
    principal_kind: str | None = None,
    redactions: dict,
    priority: int = 0,
) -> Rule:
    """Helper to create a redaction rule."""
    conds: dict = {
        "object_type": object_type,
        "redactions": redactions,
    }
    if principal_kind:
        conds["principal_kind"] = principal_kind
    return rule_store.create_rule(
        name="test redaction",
        rule_type=RuleType.REDACTION,
        effect=RuleEffect.DENY,
        conditions=conds,
        priority=priority,
        created_by=created_by,
    )


# -----------------------------------------------------------------------
# FieldRedaction
# -----------------------------------------------------------------------

class TestFieldRedaction:

    def test_from_dict_mask(self):
        fr = FieldRedaction.from_dict("card", {"strategy": "mask", "visible_chars": 4})
        assert fr.strategy == RedactionStrategy.MASK
        assert fr.visible_chars == 4
        assert fr.mask_char == "*"

    def test_from_dict_replace(self):
        fr = FieldRedaction.from_dict("name", {"strategy": "replace", "replacement": "XXX"})
        assert fr.strategy == RedactionStrategy.REPLACE
        assert fr.replacement == "XXX"

    def test_from_dict_omit(self):
        fr = FieldRedaction.from_dict("cvv", {"strategy": "omit"})
        assert fr.strategy == RedactionStrategy.OMIT

    def test_from_dict_defaults(self):
        fr = FieldRedaction.from_dict("x", {})
        assert fr.strategy == RedactionStrategy.REPLACE
        assert fr.replacement == "[REDACTED]"


# -----------------------------------------------------------------------
# RedactionEngine — masking strategies
# -----------------------------------------------------------------------

class TestRedactionStrategies:

    def test_mask_full(self, rule_store, admin):
        rule = _make_redaction_rule(
            rule_store, created_by=admin.id,
            redactions={"card_number": {"strategy": "mask"}},
        )
        engine = RedactionEngine([rule])
        result = engine.apply(
            {"card_number": "4111111111111111"},
            object_type="CreditCard",
        )
        assert result.data["card_number"] == "****************"
        assert "card_number" in result.redacted_fields

    def test_mask_with_visible_chars(self, rule_store, admin):
        rule = _make_redaction_rule(
            rule_store, created_by=admin.id,
            redactions={"card_number": {"strategy": "mask", "visible_chars": 4}},
        )
        engine = RedactionEngine([rule])
        result = engine.apply(
            {"card_number": "4111111111111111"},
            object_type="CreditCard",
        )
        assert result.data["card_number"] == "************1111"

    def test_mask_custom_char(self, rule_store, admin):
        rule = _make_redaction_rule(
            rule_store, created_by=admin.id,
            redactions={"ssn": {"strategy": "mask", "mask_char": "X", "visible_chars": 4}},
        )
        engine = RedactionEngine([rule])
        result = engine.apply(
            {"ssn": "123-45-6789"},
            object_type="CreditCard",
        )
        assert result.data["ssn"] == "XXXXXXX6789"

    def test_replace(self, rule_store, admin):
        rule = _make_redaction_rule(
            rule_store, created_by=admin.id,
            redactions={"holder_name": {"strategy": "replace", "replacement": "[HIDDEN]"}},
        )
        engine = RedactionEngine([rule])
        result = engine.apply(
            {"holder_name": "John Doe"},
            object_type="CreditCard",
        )
        assert result.data["holder_name"] == "[HIDDEN]"

    def test_omit(self, rule_store, admin):
        rule = _make_redaction_rule(
            rule_store, created_by=admin.id,
            redactions={"cvv": {"strategy": "omit"}},
        )
        engine = RedactionEngine([rule])
        result = engine.apply(
            {"cvv": "123", "name": "Card"},
            object_type="CreditCard",
        )
        assert "cvv" not in result.data
        assert result.data["name"] == "Card"
        assert "cvv" in result.redacted_fields


# -----------------------------------------------------------------------
# RedactionEngine — condition matching
# -----------------------------------------------------------------------

class TestRedactionConditions:

    def test_matches_object_type(self, rule_store, admin):
        rule = _make_redaction_rule(
            rule_store, created_by=admin.id,
            object_type="CreditCard",
            redactions={"card_number": {"strategy": "mask"}},
        )
        engine = RedactionEngine([rule])

        # Matches CreditCard
        result = engine.apply({"card_number": "1234"}, object_type="CreditCard")
        assert "card_number" in result.redacted_fields

        # Does NOT match Invoice
        result = engine.apply({"card_number": "1234"}, object_type="Invoice")
        assert len(result.redacted_fields) == 0

    def test_matches_principal_kind(self, rule_store, admin):
        rule = _make_redaction_rule(
            rule_store, created_by=admin.id,
            principal_kind="viewer",
            redactions={"secret": {"strategy": "replace"}},
        )
        engine = RedactionEngine([rule])

        # Viewer sees redacted
        result = engine.apply({"secret": "abc"}, object_type="CreditCard", principal_kind="viewer")
        assert result.data["secret"] == "[REDACTED]"

        # Admin sees original
        result = engine.apply({"secret": "abc"}, object_type="CreditCard", principal_kind="admin")
        assert result.data["secret"] == "abc"

    def test_no_redactions_key(self, rule_store, admin):
        rule = rule_store.create_rule(
            name="empty redaction",
            rule_type=RuleType.REDACTION,
            effect=RuleEffect.DENY,
            conditions={"object_type": "X"},
            created_by=admin.id,
        )
        engine = RedactionEngine([rule])
        result = engine.apply({"field": "value"}, object_type="X")
        assert result.data == {"field": "value"}
        assert len(result.redacted_fields) == 0

    def test_field_not_present(self, rule_store, admin):
        """Redacting a field that doesn't exist in the data is a no-op."""
        rule = _make_redaction_rule(
            rule_store, created_by=admin.id,
            redactions={"nonexistent": {"strategy": "mask"}},
        )
        engine = RedactionEngine([rule])
        result = engine.apply({"other": "val"}, object_type="CreditCard")
        assert result.data == {"other": "val"}
        assert len(result.redacted_fields) == 0

    def test_original_data_unchanged(self, rule_store, admin):
        """Redaction should not mutate the input dict."""
        rule = _make_redaction_rule(
            rule_store, created_by=admin.id,
            redactions={"secret": {"strategy": "omit"}},
        )
        engine = RedactionEngine([rule])
        data = {"secret": "value", "public": "ok"}
        result = engine.apply(data, object_type="CreditCard")
        assert "secret" in data  # original untouched
        assert "secret" not in result.data

    def test_multiple_rules_applied(self, rule_store, admin):
        r1 = _make_redaction_rule(
            rule_store, created_by=admin.id,
            redactions={"field_a": {"strategy": "mask"}},
        )
        r2 = _make_redaction_rule(
            rule_store, created_by=admin.id,
            redactions={"field_b": {"strategy": "replace"}},
        )
        engine = RedactionEngine([r1, r2])
        result = engine.apply(
            {"field_a": "aaa", "field_b": "bbb"},
            object_type="CreditCard",
        )
        assert result.data["field_a"] == "***"
        assert result.data["field_b"] == "[REDACTED]"
        assert len(result.rules_applied) == 2

    def test_ignores_non_redaction_rules(self, rule_store, admin):
        access_rule = rule_store.create_rule(
            name="access",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["read"]},
            created_by=admin.id,
        )
        engine = RedactionEngine([access_rule])
        result = engine.apply({"x": "y"}, object_type="CreditCard")
        assert result.data == {"x": "y"}

    def test_ignores_archived_rules(self, rule_store, admin):
        rule = _make_redaction_rule(
            rule_store, created_by=admin.id,
            redactions={"secret": {"strategy": "mask"}},
        )
        rule_store.archive_rule(rule.id, archived_by=admin.id)
        # Re-fetch the archived rule
        archived = rule_store.get_rule(rule.id)
        engine = RedactionEngine([archived])
        result = engine.apply({"secret": "value"}, object_type="CreditCard")
        assert result.data["secret"] == "value"  # not redacted
