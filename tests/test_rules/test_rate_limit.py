"""Tests for the rate-limit checker."""

import pytest

from scoped.exceptions import RateLimitExceededError
from scoped.identity.principal import PrincipalStore
from scoped.rules.engine import RuleStore
from scoped.rules.models import RuleEffect, RuleType
from scoped.rules.rate_limit import RateLimitChecker, RateLimitConfig, RateLimitResult
from scoped.types import now_utc


@pytest.fixture
def admin(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    return store.create_principal(kind="user", display_name="Admin", principal_id="admin")


@pytest.fixture
def rule_store(sqlite_backend):
    return RuleStore(sqlite_backend)


_seq_counter = 0


def _insert_audit_entries(backend, *, action, actor_id="admin", scope_id=None, count=1):
    """Insert fake audit trail entries for rate-limit testing."""
    global _seq_counter
    from scoped.types import generate_id

    ts = now_utc().isoformat()
    for _ in range(count):
        _seq_counter += 1
        backend.execute(
            "INSERT INTO audit_trail "
            "(id, sequence, actor_id, action, target_type, target_id, "
            "scope_id, timestamp, metadata_json, hash, previous_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                generate_id(), _seq_counter, actor_id, action,
                "test", generate_id(), scope_id, ts,
                "{}", "fakehash", "",
            ),
        )


# -----------------------------------------------------------------------
# RateLimitConfig
# -----------------------------------------------------------------------

class TestRateLimitConfig:

    def test_from_rule(self, rule_store, admin):
        rule = rule_store.create_rule(
            name="limit",
            rule_type=RuleType.RATE_LIMIT,
            effect=RuleEffect.DENY,
            conditions={
                "action": ["create"],
                "rate_limit": {"max_count": 10, "window_seconds": 60},
            },
            created_by=admin.id,
        )
        config = RateLimitConfig.from_rule(rule)
        assert config is not None
        assert config.max_count == 10
        assert config.window_seconds == 60

    def test_from_rule_no_config(self, rule_store, admin):
        rule = rule_store.create_rule(
            name="no limit",
            rule_type=RuleType.RATE_LIMIT,
            effect=RuleEffect.DENY,
            conditions={"action": ["create"]},
            created_by=admin.id,
        )
        assert RateLimitConfig.from_rule(rule) is None


# -----------------------------------------------------------------------
# RateLimitChecker
# -----------------------------------------------------------------------

class TestRateLimitChecker:

    def test_no_rules_returns_none(self, sqlite_backend, admin):
        checker = RateLimitChecker(sqlite_backend, [])
        result = checker.check(action="create")
        assert result is None

    def test_under_limit_returns_none(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="limit creates",
            rule_type=RuleType.RATE_LIMIT,
            effect=RuleEffect.DENY,
            conditions={
                "action": ["create"],
                "rate_limit": {"max_count": 5, "window_seconds": 3600},
            },
            created_by=admin.id,
        )
        _insert_audit_entries(sqlite_backend, action="create", count=3)

        checker = RateLimitChecker(sqlite_backend, [rule])
        result = checker.check(action="create")
        assert result is None  # 3 < 5

    def test_at_limit_returns_violation(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="limit",
            rule_type=RuleType.RATE_LIMIT,
            effect=RuleEffect.DENY,
            conditions={
                "action": ["create"],
                "rate_limit": {"max_count": 5, "window_seconds": 3600},
            },
            created_by=admin.id,
        )
        _insert_audit_entries(sqlite_backend, action="create", count=5)

        checker = RateLimitChecker(sqlite_backend, [rule])
        result = checker.check(action="create")
        assert result is not None
        assert not result.allowed
        assert result.current_count == 5
        assert result.max_count == 5
        assert result.rule_id == rule.id

    def test_over_limit(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="limit",
            rule_type=RuleType.RATE_LIMIT,
            effect=RuleEffect.DENY,
            conditions={
                "action": ["update"],
                "rate_limit": {"max_count": 3, "window_seconds": 3600},
            },
            created_by=admin.id,
        )
        _insert_audit_entries(sqlite_backend, action="update", count=10)

        checker = RateLimitChecker(sqlite_backend, [rule])
        result = checker.check(action="update")
        assert not result.allowed
        assert result.current_count == 10

    def test_different_action_not_counted(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="limit creates only",
            rule_type=RuleType.RATE_LIMIT,
            effect=RuleEffect.DENY,
            conditions={
                "action": ["create"],
                "rate_limit": {"max_count": 2, "window_seconds": 3600},
            },
            created_by=admin.id,
        )
        _insert_audit_entries(sqlite_backend, action="update", count=100)

        checker = RateLimitChecker(sqlite_backend, [rule])
        # Checking "create" — update entries don't count
        assert checker.check(action="create") is None

    def test_action_not_matching_rule(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="limit creates",
            rule_type=RuleType.RATE_LIMIT,
            effect=RuleEffect.DENY,
            conditions={
                "action": ["create"],
                "rate_limit": {"max_count": 1, "window_seconds": 3600},
            },
            created_by=admin.id,
        )
        _insert_audit_entries(sqlite_backend, action="create", count=100)

        checker = RateLimitChecker(sqlite_backend, [rule])
        # Checking a different action — rule doesn't apply
        assert checker.check(action="delete") is None

    def test_scoped_rate_limit(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="limit per scope",
            rule_type=RuleType.RATE_LIMIT,
            effect=RuleEffect.DENY,
            conditions={
                "action": ["create"],
                "rate_limit": {"max_count": 2, "window_seconds": 3600},
            },
            created_by=admin.id,
        )
        _insert_audit_entries(sqlite_backend, action="create", scope_id="s1", count=5)
        _insert_audit_entries(sqlite_backend, action="create", scope_id="s2", count=1)

        checker = RateLimitChecker(sqlite_backend, [rule])
        # Scope s1 over limit
        result = checker.check(action="create", scope_id="s1")
        assert result is not None
        assert not result.allowed

        # Scope s2 under limit
        assert checker.check(action="create", scope_id="s2") is None

    def test_per_principal_rate_limit(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="limit per user",
            rule_type=RuleType.RATE_LIMIT,
            effect=RuleEffect.DENY,
            conditions={
                "action": ["create"],
                "rate_limit": {"max_count": 2, "window_seconds": 3600},
            },
            created_by=admin.id,
        )
        _insert_audit_entries(sqlite_backend, action="create", actor_id="user-a", count=5)
        _insert_audit_entries(sqlite_backend, action="create", actor_id="user-b", count=1)

        checker = RateLimitChecker(sqlite_backend, [rule])
        result = checker.check(action="create", principal_id="user-a")
        assert not result.allowed

        assert checker.check(action="create", principal_id="user-b") is None

    def test_check_or_raise_passes(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="limit",
            rule_type=RuleType.RATE_LIMIT,
            effect=RuleEffect.DENY,
            conditions={
                "action": ["create"],
                "rate_limit": {"max_count": 10, "window_seconds": 3600},
            },
            created_by=admin.id,
        )
        checker = RateLimitChecker(sqlite_backend, [rule])
        checker.check_or_raise(action="create")  # should not raise

    def test_check_or_raise_raises(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="limit",
            rule_type=RuleType.RATE_LIMIT,
            effect=RuleEffect.DENY,
            conditions={
                "action": ["create"],
                "rate_limit": {"max_count": 1, "window_seconds": 3600},
            },
            created_by=admin.id,
        )
        _insert_audit_entries(sqlite_backend, action="create", count=5)

        checker = RateLimitChecker(sqlite_backend, [rule])
        with pytest.raises(RateLimitExceededError) as exc_info:
            checker.check_or_raise(action="create")
        assert exc_info.value.context["max_count"] == 1

    def test_no_action_filter_matches_all(self, sqlite_backend, rule_store, admin):
        """A rate limit rule with no action condition matches any action."""
        rule = rule_store.create_rule(
            name="global limit",
            rule_type=RuleType.RATE_LIMIT,
            effect=RuleEffect.DENY,
            conditions={
                "rate_limit": {"max_count": 2, "window_seconds": 3600},
            },
            created_by=admin.id,
        )
        _insert_audit_entries(sqlite_backend, action="create", count=3)

        checker = RateLimitChecker(sqlite_backend, [rule])
        result = checker.check(action="create")
        assert not result.allowed

    def test_retry_after_seconds(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="limit",
            rule_type=RuleType.RATE_LIMIT,
            effect=RuleEffect.DENY,
            conditions={
                "action": ["create"],
                "rate_limit": {"max_count": 1, "window_seconds": 120},
            },
            created_by=admin.id,
        )
        _insert_audit_entries(sqlite_backend, action="create", count=1)

        checker = RateLimitChecker(sqlite_backend, [rule])
        result = checker.check(action="create")
        assert result.retry_after_seconds == 120
