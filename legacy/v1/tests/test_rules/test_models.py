"""Tests for rule data models."""

from scoped.rules.models import (
    BindingTargetType,
    EvaluationResult,
    Rule,
    RuleBinding,
    RuleEffect,
    RuleType,
    RuleVersion,
)
from scoped.types import Lifecycle, now_utc


class TestRule:

    def test_snapshot(self):
        ts = now_utc()
        rule = Rule(
            id="r1", name="Allow reads", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW, priority=10,
            conditions={"action": ["read"]},
            created_at=ts, created_by="admin",
        )
        snap = rule.snapshot()
        assert snap["id"] == "r1"
        assert snap["rule_type"] == "access"
        assert snap["effect"] == "ALLOW"
        assert snap["conditions"] == {"action": ["read"]}

    def test_is_active(self):
        rule = Rule(
            id="r", name="R", rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW, priority=0,
            conditions={}, created_at=now_utc(), created_by="u",
        )
        assert rule.is_active


class TestRuleVersion:

    def test_snapshot(self):
        ts = now_utc()
        ver = RuleVersion(
            id="v1", rule_id="r1", version=2,
            conditions={"action": "read"}, effect=RuleEffect.DENY,
            priority=5, created_at=ts, created_by="admin",
            change_reason="tightened",
        )
        snap = ver.snapshot()
        assert snap["version"] == 2
        assert snap["effect"] == "DENY"
        assert snap["change_reason"] == "tightened"


class TestRuleBinding:

    def test_snapshot(self):
        ts = now_utc()
        binding = RuleBinding(
            id="b1", rule_id="r1",
            target_type=BindingTargetType.SCOPE, target_id="s1",
            bound_at=ts, bound_by="admin",
        )
        snap = binding.snapshot()
        assert snap["target_type"] == "scope"
        assert snap["target_id"] == "s1"

    def test_is_active(self):
        binding = RuleBinding(
            id="b", rule_id="r",
            target_type=BindingTargetType.SCOPE, target_id="s",
            bound_at=now_utc(), bound_by="u",
        )
        assert binding.is_active


class TestEvaluationResult:

    def test_allowed(self):
        r = EvaluationResult(
            allowed=True, matching_rules=(), deny_rules=(), allow_rules=(),
        )
        assert bool(r) is True
        assert "allowed=True" in repr(r)

    def test_denied(self):
        r = EvaluationResult(
            allowed=False, matching_rules=(), deny_rules=(), allow_rules=(),
        )
        assert bool(r) is False
        assert "allowed=False" in repr(r)


class TestEnums:

    def test_rule_types(self):
        assert RuleType.ACCESS.value == "access"
        assert RuleType.SHARING.value == "sharing"
        assert RuleType.CONSTRAINT.value == "constraint"

    def test_rule_effects(self):
        assert RuleEffect.ALLOW.value == "ALLOW"
        assert RuleEffect.DENY.value == "DENY"

    def test_binding_target_types(self):
        assert BindingTargetType.SCOPE.value == "scope"
        assert BindingTargetType.PRINCIPAL.value == "principal"
        assert BindingTargetType.OBJECT.value == "object"
