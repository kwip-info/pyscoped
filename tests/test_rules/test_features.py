"""Tests for the feature-flag engine."""

import pytest

from scoped.identity.principal import PrincipalStore
from scoped.rules.engine import RuleStore
from scoped.rules.models import RuleEffect, RuleType
from scoped.rules.features import FeatureFlagConfig, FeatureFlagEngine, FeatureFlagResult


@pytest.fixture
def admin(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    return store.create_principal(kind="user", display_name="Admin", principal_id="admin")


@pytest.fixture
def rule_store(sqlite_backend):
    return RuleStore(sqlite_backend)


def _make_flag_rule(
    rule_store,
    *,
    created_by: str,
    feature_name: str,
    enabled: bool = True,
    rollout_percentage: int = 100,
    scope_id: str | None = None,
    priority: int = 0,
):
    conds = {
        "feature_flag": {
            "feature_name": feature_name,
            "enabled": enabled,
            "rollout_percentage": rollout_percentage,
        },
    }
    if scope_id:
        conds["scope_id"] = scope_id
    return rule_store.create_rule(
        name=f"flag: {feature_name}",
        rule_type=RuleType.FEATURE_FLAG,
        effect=RuleEffect.ALLOW if enabled else RuleEffect.DENY,
        conditions=conds,
        priority=priority,
        created_by=created_by,
    )


# -----------------------------------------------------------------------
# FeatureFlagConfig
# -----------------------------------------------------------------------

class TestFeatureFlagConfig:

    def test_from_rule(self, rule_store, admin):
        rule = _make_flag_rule(rule_store, created_by=admin.id, feature_name="dark_mode")
        config = FeatureFlagConfig.from_rule(rule)
        assert config is not None
        assert config.feature_name == "dark_mode"
        assert config.enabled is True
        assert config.rollout_percentage == 100

    def test_from_rule_no_config(self, rule_store, admin):
        rule = rule_store.create_rule(
            name="not a flag",
            rule_type=RuleType.FEATURE_FLAG,
            effect=RuleEffect.ALLOW,
            conditions={},
            created_by=admin.id,
        )
        assert FeatureFlagConfig.from_rule(rule) is None


# -----------------------------------------------------------------------
# FeatureFlagEngine
# -----------------------------------------------------------------------

class TestFeatureFlagEngine:

    def test_flag_not_found(self, sqlite_backend, admin):
        engine = FeatureFlagEngine(sqlite_backend, [])
        result = engine.is_enabled("nonexistent")
        assert not result.enabled
        assert result.rule_id is None
        assert result.rollout_percentage == 0

    def test_flag_enabled(self, sqlite_backend, rule_store, admin):
        rule = _make_flag_rule(rule_store, created_by=admin.id, feature_name="dark_mode")
        engine = FeatureFlagEngine(sqlite_backend, [rule])
        result = engine.is_enabled("dark_mode")
        assert result.enabled
        assert result.feature_name == "dark_mode"
        assert result.rule_id == rule.id

    def test_flag_disabled(self, sqlite_backend, rule_store, admin):
        rule = _make_flag_rule(
            rule_store, created_by=admin.id,
            feature_name="beta", enabled=False,
        )
        engine = FeatureFlagEngine(sqlite_backend, [rule])
        result = engine.is_enabled("beta")
        assert not result.enabled

    def test_priority_wins(self, sqlite_backend, rule_store, admin):
        """Higher priority rule takes precedence."""
        low = _make_flag_rule(
            rule_store, created_by=admin.id,
            feature_name="feat", enabled=True, priority=0,
        )
        high = _make_flag_rule(
            rule_store, created_by=admin.id,
            feature_name="feat", enabled=False, priority=10,
        )
        engine = FeatureFlagEngine(sqlite_backend, [low, high])
        result = engine.is_enabled("feat")
        assert not result.enabled
        assert result.rule_id == high.id

    def test_scoped_flag(self, sqlite_backend, rule_store, admin):
        rule = _make_flag_rule(
            rule_store, created_by=admin.id,
            feature_name="scoped_feat", scope_id="s1",
        )
        engine = FeatureFlagEngine(sqlite_backend, [rule])

        # Matches scope
        result = engine.is_enabled("scoped_feat", scope_id="s1")
        assert result.enabled

        # Different scope — flag rule has scope_id condition, doesn't match s2
        result = engine.is_enabled("scoped_feat", scope_id="s2")
        assert not result.enabled

    def test_rollout_percentage_100(self, sqlite_backend, rule_store, admin):
        rule = _make_flag_rule(
            rule_store, created_by=admin.id,
            feature_name="full_rollout", rollout_percentage=100,
        )
        engine = FeatureFlagEngine(sqlite_backend, [rule])
        # Should be enabled for everyone
        result = engine.is_enabled("full_rollout", principal_id="any-user")
        assert result.enabled

    def test_rollout_percentage_0(self, sqlite_backend, rule_store, admin):
        rule = _make_flag_rule(
            rule_store, created_by=admin.id,
            feature_name="no_rollout", rollout_percentage=0,
        )
        engine = FeatureFlagEngine(sqlite_backend, [rule])
        # Should be disabled for everyone
        result = engine.is_enabled("no_rollout", principal_id="any-user")
        assert not result.enabled

    def test_rollout_deterministic(self, sqlite_backend, rule_store, admin):
        """Same principal+feature always gets the same result."""
        rule = _make_flag_rule(
            rule_store, created_by=admin.id,
            feature_name="partial", rollout_percentage=50,
        )
        engine = FeatureFlagEngine(sqlite_backend, [rule])

        results = set()
        for _ in range(10):
            r = engine.is_enabled("partial", principal_id="user-42")
            results.add(r.enabled)
        # Should always be the same value (deterministic)
        assert len(results) == 1

    def test_rollout_varies_by_principal(self, sqlite_backend, rule_store, admin):
        """Different principals may get different results at 50%."""
        rule = _make_flag_rule(
            rule_store, created_by=admin.id,
            feature_name="split", rollout_percentage=50,
        )
        engine = FeatureFlagEngine(sqlite_backend, [rule])

        # With enough principals, some should be enabled and some not
        results = set()
        for i in range(100):
            r = engine.is_enabled("split", principal_id=f"user-{i}")
            results.add(r.enabled)
        assert True in results
        assert False in results

    def test_rollout_no_principal(self, sqlite_backend, rule_store, admin):
        """Without principal_id, rollout < 100 still enables (no hash check)."""
        rule = _make_flag_rule(
            rule_store, created_by=admin.id,
            feature_name="partial_no_user", rollout_percentage=50,
        )
        engine = FeatureFlagEngine(sqlite_backend, [rule])
        result = engine.is_enabled("partial_no_user")
        # No principal_id → skip rollout check → enabled
        assert result.enabled

    def test_list_flags(self, sqlite_backend, rule_store, admin):
        _make_flag_rule(rule_store, created_by=admin.id, feature_name="flag_a")
        _make_flag_rule(rule_store, created_by=admin.id, feature_name="flag_b")
        rules = rule_store.list_rules(rule_type=RuleType.FEATURE_FLAG)
        engine = FeatureFlagEngine(sqlite_backend, rules)
        flags = engine.list_flags()
        names = {f.feature_name for f in flags}
        assert "flag_a" in names
        assert "flag_b" in names

    def test_list_flags_deduplicates(self, sqlite_backend, rule_store, admin):
        """Multiple rules for same feature → only listed once."""
        _make_flag_rule(rule_store, created_by=admin.id, feature_name="dup", priority=0)
        _make_flag_rule(rule_store, created_by=admin.id, feature_name="dup", priority=5)
        rules = rule_store.list_rules(rule_type=RuleType.FEATURE_FLAG)
        engine = FeatureFlagEngine(sqlite_backend, rules)
        flags = engine.list_flags()
        assert sum(1 for f in flags if f.feature_name == "dup") == 1

    def test_ignores_non_flag_rules(self, sqlite_backend, rule_store, admin):
        access_rule = rule_store.create_rule(
            name="access",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["read"]},
            created_by=admin.id,
        )
        engine = FeatureFlagEngine(sqlite_backend, [access_rule])
        assert len(engine.list_flags()) == 0

    def test_ignores_archived_flags(self, sqlite_backend, rule_store, admin):
        rule = _make_flag_rule(rule_store, created_by=admin.id, feature_name="old")
        rule_store.archive_rule(rule.id, archived_by=admin.id)
        archived = rule_store.get_rule(rule.id)
        engine = FeatureFlagEngine(sqlite_backend, [archived])
        result = engine.is_enabled("old")
        assert not result.enabled
