"""Tests for rule engine caching — TTL, invalidation, and stats."""

import time

import pytest

from scoped.identity.principal import PrincipalStore
from scoped.rules.cache import RuleCache
from scoped.rules.engine import RuleEngine, RuleStore
from scoped.rules.models import BindingTargetType, RuleEffect, RuleType


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    admin = store.create_principal(kind="user", display_name="Admin", principal_id="admin")
    return (admin,)


@pytest.fixture
def store(sqlite_backend):
    return RuleStore(sqlite_backend)


@pytest.fixture
def cached_engine(sqlite_backend):
    return RuleEngine(sqlite_backend, cache_ttl=60.0)


class TestCacheHit:

    def test_cache_hit(self, store, cached_engine, principals):
        """Evaluate twice; second call uses cache (verify via stats)."""
        (admin,) = principals
        rule = store.create_rule(
            name="Allow read",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["read"]},
            created_by=admin.id,
        )
        store.bind_rule(
            rule.id,
            target_type=BindingTargetType.SCOPE,
            target_id="s1",
            bound_by=admin.id,
        )

        # First evaluate — cache miss
        result1 = cached_engine.evaluate(action="read", scope_id="s1")
        assert result1.allowed

        stats_after_first = cached_engine._cache.stats()
        assert stats_after_first["misses"] >= 1

        # Second evaluate — cache hit
        result2 = cached_engine.evaluate(action="read", scope_id="s1")
        assert result2.allowed

        stats_after_second = cached_engine._cache.stats()
        assert stats_after_second["hits"] >= 1


class TestCacheTTLExpiry:

    def test_cache_ttl_expiry(self, sqlite_backend, store, principals):
        """Set TTL=0.01s, sleep briefly, verify cache miss."""
        (admin,) = principals
        engine = RuleEngine(sqlite_backend, cache_ttl=0.01)

        rule = store.create_rule(
            name="Allow read",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["read"]},
            created_by=admin.id,
        )
        store.bind_rule(
            rule.id,
            target_type=BindingTargetType.SCOPE,
            target_id="s1",
            bound_by=admin.id,
        )

        # First evaluate — populates cache
        engine.evaluate(action="read", scope_id="s1")
        first_misses = engine._cache.stats()["misses"]

        # Wait for TTL to expire
        time.sleep(0.05)

        # Second evaluate — cache expired, so another miss
        engine.evaluate(action="read", scope_id="s1")
        second_misses = engine._cache.stats()["misses"]
        assert second_misses > first_misses


class TestCacheInvalidationOnCreate:

    def test_cache_invalidation_on_create(self, sqlite_backend, principals):
        """Creating a rule via RuleStore invalidates the shared cache."""
        (admin,) = principals
        engine = RuleEngine(sqlite_backend, cache_ttl=60.0)
        rule_store = RuleStore(sqlite_backend)
        rule_store.set_cache(engine._cache)

        # Seed a rule + binding so the cache has something
        rule = rule_store.create_rule(
            name="Allow read",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["read"]},
            created_by=admin.id,
        )
        rule_store.bind_rule(
            rule.id,
            target_type=BindingTargetType.SCOPE,
            target_id="s1",
            bound_by=admin.id,
        )

        # Populate cache
        engine.evaluate(action="read", scope_id="s1")
        assert engine._cache.stats()["size"] >= 1

        # Create a new rule — should invalidate entire cache
        rule_store.create_rule(
            name="Deny write",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY,
            conditions={"action": ["write"]},
            created_by=admin.id,
        )

        assert engine._cache.stats()["size"] == 0


class TestCacheInvalidationOnBind:

    def test_cache_invalidation_on_bind(self, sqlite_backend, principals):
        """Binding a rule invalidates the shared cache."""
        (admin,) = principals
        engine = RuleEngine(sqlite_backend, cache_ttl=60.0)
        rule_store = RuleStore(sqlite_backend)
        rule_store.set_cache(engine._cache)

        rule = rule_store.create_rule(
            name="Allow read",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": ["read"]},
            created_by=admin.id,
        )

        # Evaluate to populate cache (no bindings yet, so default-deny)
        result = engine.evaluate(action="read", scope_id="s1")
        assert not result.allowed
        assert engine._cache.stats()["size"] >= 1

        # Bind the rule — should invalidate cache
        rule_store.bind_rule(
            rule.id,
            target_type=BindingTargetType.SCOPE,
            target_id="s1",
            bound_by=admin.id,
        )

        assert engine._cache.stats()["size"] == 0

        # Re-evaluate — should now find the rule
        result2 = engine.evaluate(action="read", scope_id="s1")
        assert result2.allowed


class TestCacheDisabledByDefault:

    def test_cache_disabled_by_default(self, sqlite_backend):
        """RuleEngine without cache_ttl has no cache."""
        engine = RuleEngine(sqlite_backend)
        assert engine._cache is None


class TestCacheStats:

    def test_cache_stats(self):
        """Verify hits, misses, and hit_rate tracking."""
        cache = RuleCache(ttl_seconds=60.0)

        stats = cache.stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["hit_rate"] == 0.0
        assert stats["size"] == 0
        assert stats["ttl_seconds"] == 60.0

        # Miss
        cache.get("nonexistent")
        stats = cache.stats()
        assert stats["misses"] == 1
        assert stats["hits"] == 0
        assert stats["hit_rate"] == 0.0

        # Put + hit
        cache.put("key1", ["rule1"])
        result = cache.get("key1")
        assert result == ["rule1"]

        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5
        assert stats["size"] == 1
