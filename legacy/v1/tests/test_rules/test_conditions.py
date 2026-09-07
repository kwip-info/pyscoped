"""Tests for typed rule conditions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scoped.rules.conditions import (
    AccessCondition,
    FeatureFlagCondition,
    FeatureFlagSpec,
    QuotaCondition,
    QuotaSpec,
    RateLimitCondition,
    RateLimitSpec,
    RedactionCondition,
    RedactionSpec,
    conditions_to_dict,
    parse_conditions,
)
from scoped.rules.models import RuleType


class TestAccessCondition:
    def test_basic(self):
        c = AccessCondition(action="create", object_type="invoice")
        assert c.action == "create"
        assert c.object_type == "invoice"

    def test_list_actions(self):
        c = AccessCondition(action=["create", "read"])
        assert c.action == ["create", "read"]

    def test_empty(self):
        c = AccessCondition()
        assert c.action is None
        assert c.object_type is None

    def test_with_role(self):
        c = AccessCondition(action="create", role="admin")
        assert c.role == "admin"


class TestRateLimitCondition:
    def test_basic(self):
        c = RateLimitCondition(
            action=["create"],
            rate_limit=RateLimitSpec(max_count=100, window_seconds=3600),
        )
        assert c.rate_limit.max_count == 100
        assert c.rate_limit.window_seconds == 3600

    def test_missing_spec_raises(self):
        with pytest.raises(ValidationError):
            RateLimitCondition(action="create")


class TestQuotaCondition:
    def test_basic(self):
        c = QuotaCondition(
            object_type="invoice",
            quota=QuotaSpec(max_count=1000),
        )
        assert c.quota.max_count == 1000
        assert c.quota.count_table == "scoped_objects"

    def test_custom_table(self):
        c = QuotaCondition(
            quota=QuotaSpec(max_count=50, count_table="secrets", count_column="classification"),
        )
        assert c.quota.count_table == "secrets"


class TestRedactionCondition:
    def test_basic(self):
        c = RedactionCondition(
            object_type="user",
            redactions={"ssn": RedactionSpec(strategy="mask", visible_chars=4)},
        )
        assert c.redactions["ssn"].strategy == "mask"
        assert c.redactions["ssn"].visible_chars == 4


class TestFeatureFlagCondition:
    def test_basic(self):
        c = FeatureFlagCondition(
            feature_flag=FeatureFlagSpec(feature_name="dark_mode"),
        )
        assert c.feature_flag.feature_name == "dark_mode"
        assert c.feature_flag.enabled is True
        assert c.feature_flag.rollout_percentage == 100


class TestParseConditions:
    def test_access(self):
        c = parse_conditions({"action": "create"}, RuleType.ACCESS)
        assert isinstance(c, AccessCondition)
        assert c.action == "create"

    def test_rate_limit(self):
        c = parse_conditions(
            {"action": "create", "rate_limit": {"max_count": 10, "window_seconds": 60}},
            RuleType.RATE_LIMIT,
        )
        assert isinstance(c, RateLimitCondition)
        assert c.rate_limit.max_count == 10

    def test_quota(self):
        c = parse_conditions(
            {"object_type": "doc", "quota": {"max_count": 500}},
            RuleType.QUOTA,
        )
        assert isinstance(c, QuotaCondition)

    def test_redaction(self):
        c = parse_conditions(
            {"object_type": "user", "redactions": {"ssn": {"strategy": "mask"}}},
            RuleType.REDACTION,
        )
        assert isinstance(c, RedactionCondition)

    def test_feature_flag(self):
        c = parse_conditions(
            {"feature_flag": {"feature_name": "beta"}},
            RuleType.FEATURE_FLAG,
        )
        assert isinstance(c, FeatureFlagCondition)

    def test_invalid_rate_limit_raises(self):
        with pytest.raises(ValidationError):
            parse_conditions({"action": "create"}, RuleType.RATE_LIMIT)

    def test_sharing_uses_access_model(self):
        c = parse_conditions({"action": "share"}, RuleType.SHARING)
        assert isinstance(c, AccessCondition)


class TestConditionsToDict:
    def test_from_model(self):
        c = AccessCondition(action="create", object_type="invoice")
        d = conditions_to_dict(c)
        assert d == {"action": "create", "object_type": "invoice"}

    def test_from_dict_passthrough(self):
        raw = {"action": "create", "custom_key": True}
        assert conditions_to_dict(raw) is raw

    def test_round_trip(self):
        raw = {"action": ["create", "read"], "object_type": "doc"}
        c = parse_conditions(raw, RuleType.ACCESS)
        d = conditions_to_dict(c)
        assert d == raw

    def test_none_fields_excluded(self):
        c = AccessCondition(action="create")
        d = conditions_to_dict(c)
        assert "object_type" not in d
        assert "scope_id" not in d

    def test_nested_spec_serialized(self):
        c = RateLimitCondition(
            rate_limit=RateLimitSpec(max_count=10, window_seconds=60),
        )
        d = conditions_to_dict(c)
        assert d["rate_limit"]["max_count"] == 10
        assert d["rate_limit"]["window_seconds"] == 60


class TestRuleTypedConditions:
    """Test Rule.typed_conditions property."""

    def test_access_rule(self, sqlite_backend, registry):
        from scoped.identity.principal import PrincipalStore
        from scoped.rules.engine import RuleStore
        from scoped.rules.models import RuleEffect, RuleType

        store = PrincipalStore(sqlite_backend)
        store.create_principal(kind="user", display_name="Admin", principal_id="admin")
        rules = RuleStore(sqlite_backend)
        rule = rules.create_rule(
            name="test",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": "create", "object_type": "invoice"},
            created_by="admin",
        )
        tc = rule.typed_conditions
        assert isinstance(tc, AccessCondition)
        assert tc.action == "create"
        assert tc.object_type == "invoice"

    def test_typed_model_input(self, sqlite_backend, registry):
        from scoped.identity.principal import PrincipalStore
        from scoped.rules.engine import RuleStore
        from scoped.rules.models import RuleEffect, RuleType

        store = PrincipalStore(sqlite_backend)
        store.create_principal(kind="user", display_name="Admin", principal_id="admin")
        rules = RuleStore(sqlite_backend)
        cond = AccessCondition(action=["create", "read"], object_type="doc")
        rule = rules.create_rule(
            name="typed-test",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions=cond,
            created_by="admin",
        )
        assert rule.conditions == {"action": ["create", "read"], "object_type": "doc"}
