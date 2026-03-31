"""Tests for the quota checker."""

import pytest

from scoped.exceptions import QuotaExceededError
from scoped.identity.principal import PrincipalStore
from scoped.rules.engine import RuleStore
from scoped.rules.models import RuleEffect, RuleType
from scoped.rules.quotas import QuotaChecker, QuotaConfig, QuotaResult
from scoped.types import generate_id, now_utc


@pytest.fixture
def admin(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    return store.create_principal(kind="user", display_name="Admin", principal_id="admin")


@pytest.fixture
def rule_store(sqlite_backend):
    return RuleStore(sqlite_backend)


def _insert_objects(backend, *, object_type, owner_id="admin", count=1):
    """Insert fake objects for quota counting."""
    ts = now_utc().isoformat()
    for _ in range(count):
        backend.execute(
            "INSERT INTO scoped_objects "
            "(id, object_type, owner_id, current_version, created_at, lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (generate_id(), object_type, owner_id, 1, ts, "ACTIVE"),
        )


# -----------------------------------------------------------------------
# QuotaConfig
# -----------------------------------------------------------------------

class TestQuotaConfig:

    def test_from_rule(self, rule_store, admin):
        rule = rule_store.create_rule(
            name="quota",
            rule_type=RuleType.QUOTA,
            effect=RuleEffect.DENY,
            conditions={
                "object_type": "Document",
                "quota": {"max_count": 100},
            },
            created_by=admin.id,
        )
        config = QuotaConfig.from_rule(rule)
        assert config is not None
        assert config.max_count == 100
        assert config.count_table == "scoped_objects"
        assert config.count_column == "object_type"
        assert config.count_value == "Document"

    def test_from_rule_custom_table(self, rule_store, admin):
        rule = rule_store.create_rule(
            name="quota",
            rule_type=RuleType.QUOTA,
            effect=RuleEffect.DENY,
            conditions={
                "object_type": "Secret",
                "quota": {
                    "max_count": 50,
                    "count_table": "secrets",
                    "count_column": "lifecycle",
                    "count_value": "ACTIVE",
                },
            },
            created_by=admin.id,
        )
        config = QuotaConfig.from_rule(rule)
        assert config.count_table == "secrets"

    def test_from_rule_no_config(self, rule_store, admin):
        rule = rule_store.create_rule(
            name="no quota",
            rule_type=RuleType.QUOTA,
            effect=RuleEffect.DENY,
            conditions={"object_type": "X"},
            created_by=admin.id,
        )
        assert QuotaConfig.from_rule(rule) is None


# -----------------------------------------------------------------------
# QuotaChecker
# -----------------------------------------------------------------------

class TestQuotaChecker:

    def test_no_rules_returns_none(self, sqlite_backend):
        checker = QuotaChecker(sqlite_backend, [])
        assert checker.check(object_type="Document") is None

    def test_under_quota(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="doc quota",
            rule_type=RuleType.QUOTA,
            effect=RuleEffect.DENY,
            conditions={
                "object_type": "Document",
                "quota": {"max_count": 10},
            },
            created_by=admin.id,
        )
        _insert_objects(sqlite_backend, object_type="Document", count=5)

        checker = QuotaChecker(sqlite_backend, [rule])
        assert checker.check(object_type="Document") is None

    def test_at_quota(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="doc quota",
            rule_type=RuleType.QUOTA,
            effect=RuleEffect.DENY,
            conditions={
                "object_type": "Document",
                "quota": {"max_count": 5},
            },
            created_by=admin.id,
        )
        _insert_objects(sqlite_backend, object_type="Document", count=5)

        checker = QuotaChecker(sqlite_backend, [rule])
        result = checker.check(object_type="Document")
        assert result is not None
        assert not result.allowed
        assert result.current_count == 5
        assert result.max_count == 5

    def test_over_quota(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="quota",
            rule_type=RuleType.QUOTA,
            effect=RuleEffect.DENY,
            conditions={
                "object_type": "Document",
                "quota": {"max_count": 3},
            },
            created_by=admin.id,
        )
        _insert_objects(sqlite_backend, object_type="Document", count=10)

        checker = QuotaChecker(sqlite_backend, [rule])
        result = checker.check(object_type="Document")
        assert not result.allowed
        assert result.current_count == 10

    def test_different_type_not_counted(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="doc quota",
            rule_type=RuleType.QUOTA,
            effect=RuleEffect.DENY,
            conditions={
                "object_type": "Document",
                "quota": {"max_count": 2},
            },
            created_by=admin.id,
        )
        _insert_objects(sqlite_backend, object_type="Image", count=100)

        checker = QuotaChecker(sqlite_backend, [rule])
        assert checker.check(object_type="Document") is None

    def test_type_not_matching_rule(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="doc quota",
            rule_type=RuleType.QUOTA,
            effect=RuleEffect.DENY,
            conditions={
                "object_type": "Document",
                "quota": {"max_count": 1},
            },
            created_by=admin.id,
        )
        _insert_objects(sqlite_backend, object_type="Document", count=100)

        checker = QuotaChecker(sqlite_backend, [rule])
        # Checking a different type — rule doesn't apply
        assert checker.check(object_type="Image") is None

    def test_scoped_quota_via_owner(self, sqlite_backend, rule_store, admin, registry):
        """Quota with scope_column can filter by owner."""
        pstore = PrincipalStore(sqlite_backend)
        user_a = pstore.create_principal(kind="user", display_name="A", principal_id="user-a")
        user_b = pstore.create_principal(kind="user", display_name="B", principal_id="user-b")

        rule = rule_store.create_rule(
            name="per-owner quota",
            rule_type=RuleType.QUOTA,
            effect=RuleEffect.DENY,
            conditions={
                "object_type": "Document",
                "quota": {
                    "max_count": 3,
                    "scope_column": "owner_id",
                },
            },
            created_by=admin.id,
        )
        _insert_objects(sqlite_backend, object_type="Document", owner_id="user-a", count=5)
        _insert_objects(sqlite_backend, object_type="Document", owner_id="user-b", count=1)

        checker = QuotaChecker(sqlite_backend, [rule])
        # user-a over quota
        result = checker.check(object_type="Document", scope_id="user-a")
        assert not result.allowed

        # user-b under quota
        assert checker.check(object_type="Document", scope_id="user-b") is None

    def test_check_or_raise_passes(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="quota",
            rule_type=RuleType.QUOTA,
            effect=RuleEffect.DENY,
            conditions={
                "object_type": "Document",
                "quota": {"max_count": 100},
            },
            created_by=admin.id,
        )
        checker = QuotaChecker(sqlite_backend, [rule])
        checker.check_or_raise(object_type="Document")  # should not raise

    def test_check_or_raise_raises(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="quota",
            rule_type=RuleType.QUOTA,
            effect=RuleEffect.DENY,
            conditions={
                "object_type": "Document",
                "quota": {"max_count": 1},
            },
            created_by=admin.id,
        )
        _insert_objects(sqlite_backend, object_type="Document", count=5)

        checker = QuotaChecker(sqlite_backend, [rule])
        with pytest.raises(QuotaExceededError) as exc_info:
            checker.check_or_raise(object_type="Document")
        assert exc_info.value.context["max_count"] == 1

    def test_get_usage(self, sqlite_backend, rule_store, admin):
        rule = rule_store.create_rule(
            name="quota",
            rule_type=RuleType.QUOTA,
            effect=RuleEffect.DENY,
            conditions={
                "object_type": "Document",
                "quota": {"max_count": 10},
            },
            created_by=admin.id,
        )
        _insert_objects(sqlite_backend, object_type="Document", count=7)

        checker = QuotaChecker(sqlite_backend, [rule])
        usage = checker.get_usage(object_type="Document")
        assert rule.id in usage
        assert usage[rule.id]["current_count"] == 7
        assert usage[rule.id]["remaining"] == 3

    def test_disallowed_table_returns_zero(self, sqlite_backend, rule_store, admin):
        """Tables not in the allowlist should count as 0 (not SQL-inject)."""
        rule = rule_store.create_rule(
            name="bad table",
            rule_type=RuleType.QUOTA,
            effect=RuleEffect.DENY,
            conditions={
                "object_type": "X",
                "quota": {
                    "max_count": 1,
                    "count_table": "sqlite_master",
                    "count_column": "type",
                    "count_value": "table",
                },
            },
            created_by=admin.id,
        )
        checker = QuotaChecker(sqlite_backend, [rule])
        # Should not error or inject — just returns None (count=0, under limit)
        assert checker.check(object_type="X") is None

    def test_no_object_type_filter(self, sqlite_backend, rule_store, admin):
        """A quota rule with no object_type matches all types."""
        rule = rule_store.create_rule(
            name="global quota",
            rule_type=RuleType.QUOTA,
            effect=RuleEffect.DENY,
            conditions={
                "quota": {
                    "max_count": 3,
                    "count_table": "scoped_objects",
                    "count_column": "object_type",
                    "count_value": "Document",
                },
            },
            created_by=admin.id,
        )
        _insert_objects(sqlite_backend, object_type="Document", count=5)

        checker = QuotaChecker(sqlite_backend, [rule])
        result = checker.check(object_type="anything")
        assert not result.allowed
