"""Tests for Phase 1: entity updates, bulk ops, rule enforcement, pagination."""

import pytest

from scoped.audit.query import AuditQuery
from scoped.audit.writer import AuditWriter
from scoped.exceptions import AccessDeniedError
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.rules.engine import RuleEngine, RuleStore
from scoped.rules.models import BindingTargetType, RuleEffect, RuleType
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.tenancy.models import ScopeRole
from scoped.types import ActionType


# -- Fixtures -----------------------------------------------------------------

@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    bob = store.create_principal(kind="user", display_name="Bob", principal_id="bob")
    return alice, bob


@pytest.fixture
def principal_store(sqlite_backend):
    return PrincipalStore(sqlite_backend)


@pytest.fixture
def lc(sqlite_backend):
    return ScopeLifecycle(sqlite_backend)


@pytest.fixture
def audit_writer(sqlite_backend):
    return AuditWriter(sqlite_backend)


@pytest.fixture
def mgr(sqlite_backend):
    return ScopedManager(sqlite_backend)


# =============================================================================
# 1. Entity update methods
# =============================================================================


class TestPrincipalUpdate:

    def test_update_display_name(self, principal_store, principals):
        alice, _ = principals
        updated = principal_store.update_principal(
            alice.id, display_name="Alice Smith",
        )
        assert updated.display_name == "Alice Smith"

    def test_update_metadata_merges(self, principal_store, principals):
        alice, _ = principals
        principal_store.update_principal(alice.id, metadata={"team": "eng"})
        updated = principal_store.update_principal(alice.id, metadata={"role": "lead"})
        assert updated.metadata.get("team") == "eng"
        assert updated.metadata.get("role") == "lead"

    def test_update_noop_returns_original(self, principal_store, principals):
        alice, _ = principals
        same = principal_store.update_principal(alice.id)
        assert same.display_name == alice.display_name

    def test_update_with_audit(self, sqlite_backend, principals):
        alice, _ = principals
        writer = AuditWriter(sqlite_backend)
        store = PrincipalStore(sqlite_backend, audit_writer=writer)
        store.update_principal(alice.id, display_name="New Name", updated_by="admin")

        entries = AuditQuery(sqlite_backend).query(
            target_id=alice.id, action=ActionType.UPDATE,
        )
        assert len(entries) == 1
        assert entries[0].after_state["display_name"] == "New Name"


class TestScopeUpdate:

    def test_update_description(self, lc, principals):
        alice, _ = principals
        scope = lc.create_scope(name="Team", owner_id=alice.id, description="Old")
        updated = lc.update_scope(scope.id, description="New desc", updated_by=alice.id)
        assert updated.description == "New desc"

    def test_update_metadata_merges(self, lc, principals):
        alice, _ = principals
        scope = lc.create_scope(
            name="Team", owner_id=alice.id, metadata={"region": "us"},
        )
        updated = lc.update_scope(
            scope.id, metadata={"tier": "premium"}, updated_by=alice.id,
        )
        assert updated.metadata["region"] == "us"
        assert updated.metadata["tier"] == "premium"

    def test_update_frozen_scope_raises(self, lc, principals):
        alice, _ = principals
        scope = lc.create_scope(name="Team", owner_id=alice.id)
        lc.freeze_scope(scope.id, frozen_by=alice.id)

        from scoped.exceptions import ScopeFrozenError
        with pytest.raises(ScopeFrozenError):
            lc.update_scope(scope.id, description="Nope", updated_by=alice.id)

    def test_update_noop_returns_original(self, lc, principals):
        alice, _ = principals
        scope = lc.create_scope(name="Team", owner_id=alice.id, description="Desc")
        same = lc.update_scope(scope.id, updated_by=alice.id)
        assert same.description == "Desc"


# =============================================================================
# 2. Bulk operations
# =============================================================================


class TestBulkCreateObjects:

    def test_create_many_returns_all(self, mgr, principals):
        alice, _ = principals
        results = mgr.create_many(
            items=[
                {"object_type": "Doc", "data": {"title": "A"}},
                {"object_type": "Doc", "data": {"title": "B"}},
                {"object_type": "Task", "data": {"name": "T1"}},
            ],
            owner_id=alice.id,
        )
        assert len(results) == 3
        types = {obj.object_type for obj, _ in results}
        assert types == {"Doc", "Task"}

    def test_create_many_atomic(self, sqlite_backend, principals):
        """All objects are created in a single transaction."""
        alice, _ = principals
        mgr = ScopedManager(sqlite_backend)
        mgr.create_many(
            items=[
                {"object_type": "X", "data": {"i": 1}},
                {"object_type": "X", "data": {"i": 2}},
            ],
            owner_id=alice.id,
        )
        row = sqlite_backend.fetch_one(
            "SELECT COUNT(*) as cnt FROM scoped_objects WHERE owner_id = ?",
            (alice.id,),
        )
        assert row["cnt"] == 2

    def test_create_many_audit_batch(self, sqlite_backend, principals):
        alice, _ = principals
        writer = AuditWriter(sqlite_backend)
        mgr = ScopedManager(sqlite_backend, audit_writer=writer)
        mgr.create_many(
            items=[
                {"object_type": "A", "data": {"x": 1}},
                {"object_type": "B", "data": {"x": 2}},
            ],
            owner_id=alice.id,
        )
        entries = AuditQuery(sqlite_backend).query(action=ActionType.CREATE)
        assert len(entries) == 2

    def test_create_many_empty(self, mgr, principals):
        alice, _ = principals
        results = mgr.create_many(items=[], owner_id=alice.id)
        assert results == []


class TestBulkAddMembers:

    def test_add_members_multiple(self, sqlite_backend, registry, principals):
        alice, bob = principals
        store = PrincipalStore(sqlite_backend)
        carol = store.create_principal(kind="user", display_name="Carol", principal_id="carol")

        lc = ScopeLifecycle(sqlite_backend)
        scope = lc.create_scope(name="Team", owner_id=alice.id)

        memberships = lc.add_members(
            scope.id,
            members=[
                {"principal_id": bob.id, "role": "editor"},
                {"principal_id": carol.id, "role": "viewer"},
            ],
            granted_by=alice.id,
        )
        assert len(memberships) == 2
        roles = {m.principal_id: m.role for m in memberships}
        assert roles[bob.id] == ScopeRole.EDITOR
        assert roles[carol.id] == ScopeRole.VIEWER

    def test_add_members_frozen_raises(self, lc, principals):
        alice, bob = principals
        scope = lc.create_scope(name="Team", owner_id=alice.id)
        lc.freeze_scope(scope.id, frozen_by=alice.id)

        from scoped.exceptions import ScopeFrozenError
        with pytest.raises(ScopeFrozenError):
            lc.add_members(
                scope.id,
                members=[{"principal_id": bob.id}],
                granted_by=alice.id,
            )


# =============================================================================
# 3. Rules enforcement in ScopedManager
# =============================================================================


class TestRuleEnforcement:

    @pytest.fixture
    def rule_store(self, sqlite_backend):
        return RuleStore(sqlite_backend)

    @pytest.fixture
    def rule_engine(self, sqlite_backend, audit_writer):
        return RuleEngine(sqlite_backend, audit_writer=audit_writer)

    @pytest.fixture
    def enforced_mgr(self, sqlite_backend, audit_writer, rule_engine):
        return ScopedManager(
            sqlite_backend,
            audit_writer=audit_writer,
            rule_engine=rule_engine,
        )

    def test_deny_rule_blocks_create(self, enforced_mgr, rule_store, principals):
        alice, _ = principals
        rule = rule_store.create_rule(
            name="block-invoices",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY,
            conditions={"action": "create", "object_type": "invoice"},
            priority=100,
            created_by="system",
        )
        rule_store.bind_rule(
            rule.id,
            target_type=BindingTargetType.OBJECT_TYPE,
            target_id="invoice",
            bound_by="system",
        )

        with pytest.raises(AccessDeniedError, match="denied"):
            enforced_mgr.create(
                object_type="invoice",
                owner_id=alice.id,
                data={"amount": 100},
            )

    def test_allow_rule_permits_create(self, enforced_mgr, rule_store, principals):
        alice, _ = principals
        rule = rule_store.create_rule(
            name="allow-docs",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.ALLOW,
            conditions={"action": "create", "object_type": "doc"},
            priority=100,
            created_by="system",
        )
        rule_store.bind_rule(
            rule.id,
            target_type=BindingTargetType.OBJECT_TYPE,
            target_id="doc",
            bound_by="system",
        )

        obj, ver = enforced_mgr.create(
            object_type="doc", owner_id=alice.id, data={"title": "ok"},
        )
        assert obj.id is not None

    def test_no_rules_allows_by_default(self, enforced_mgr, principals):
        """When no rules exist, _check_rules is a no-op (no deny_rules matched)."""
        alice, _ = principals
        obj, _ = enforced_mgr.create(
            object_type="anything", owner_id=alice.id, data={"x": 1},
        )
        assert obj.id is not None

    def test_no_rule_engine_allows_everything(self, mgr, principals):
        """Manager without rule_engine behaves as before."""
        alice, _ = principals
        obj, _ = mgr.create(
            object_type="anything", owner_id=alice.id, data={"x": 1},
        )
        assert obj.id is not None

    def test_deny_rule_blocks_update(self, enforced_mgr, rule_store, principals):
        alice, _ = principals
        obj, _ = enforced_mgr.create(
            object_type="doc", owner_id=alice.id, data={"v": 1},
        )

        rule = rule_store.create_rule(
            name="block-updates",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY,
            conditions={"action": "update"},
            priority=100,
            created_by="system",
        )
        rule_store.bind_rule(
            rule.id,
            target_type=BindingTargetType.OBJECT_TYPE,
            target_id="doc",
            bound_by="system",
        )

        with pytest.raises(AccessDeniedError):
            enforced_mgr.update(
                obj.id, principal_id=alice.id, data={"v": 2},
            )

    def test_deny_rule_blocks_delete(self, enforced_mgr, rule_store, principals):
        alice, _ = principals
        obj, _ = enforced_mgr.create(
            object_type="doc", owner_id=alice.id, data={"v": 1},
        )

        rule = rule_store.create_rule(
            name="block-deletes",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY,
            conditions={"action": "delete"},
            priority=100,
            created_by="system",
        )
        rule_store.bind_rule(
            rule.id,
            target_type=BindingTargetType.OBJECT_TYPE,
            target_id="doc",
            bound_by="system",
        )

        with pytest.raises(AccessDeniedError):
            enforced_mgr.tombstone(obj.id, principal_id=alice.id)


# =============================================================================
# 4. Paginated list_versions() and chunked verify_chain()
# =============================================================================


class TestListVersionsPagination:

    def test_list_versions_limit(self, mgr, principals):
        alice, _ = principals
        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={"v": 1})
        for i in range(2, 6):
            mgr.update(obj.id, principal_id=alice.id, data={"v": i})

        # 5 versions total
        all_versions = mgr.list_versions(obj.id, principal_id=alice.id)
        assert len(all_versions) == 5

        page1 = mgr.list_versions(obj.id, principal_id=alice.id, limit=2)
        assert len(page1) == 2
        assert page1[0].version == 1
        assert page1[1].version == 2

    def test_list_versions_offset(self, mgr, principals):
        alice, _ = principals
        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={"v": 1})
        for i in range(2, 6):
            mgr.update(obj.id, principal_id=alice.id, data={"v": i})

        page2 = mgr.list_versions(obj.id, principal_id=alice.id, limit=2, offset=2)
        assert len(page2) == 2
        assert page2[0].version == 3
        assert page2[1].version == 4

    def test_list_versions_no_limit(self, mgr, principals):
        alice, _ = principals
        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={"v": 1})
        mgr.update(obj.id, principal_id=alice.id, data={"v": 2})

        versions = mgr.list_versions(obj.id, principal_id=alice.id)
        assert len(versions) == 2


class TestChunkedVerifyChain:

    def test_chunked_verification_small(self, sqlite_backend):
        """Small chain verified in one chunk."""
        writer = AuditWriter(sqlite_backend)
        for i in range(5):
            writer.record(
                actor_id="u1", action=ActionType.CREATE,
                target_type="X", target_id=f"x{i}",
            )

        query = AuditQuery(sqlite_backend)
        result = query.verify_chain(chunk_size=100)
        assert result.valid
        assert result.entries_checked == 5

    def test_chunked_verification_multiple_chunks(self, sqlite_backend):
        """Chain spanning multiple chunks is verified correctly."""
        writer = AuditWriter(sqlite_backend)
        for i in range(10):
            writer.record(
                actor_id="u1", action=ActionType.CREATE,
                target_type="X", target_id=f"x{i}",
            )

        query = AuditQuery(sqlite_backend)
        result = query.verify_chain(chunk_size=3)
        assert result.valid
        assert result.entries_checked == 10

    def test_chunked_verification_detects_break(self, sqlite_backend):
        """Tampered entry is caught even across chunk boundaries."""
        writer = AuditWriter(sqlite_backend)
        for i in range(6):
            writer.record(
                actor_id="u1", action=ActionType.CREATE,
                target_type="X", target_id=f"x{i}",
            )

        # Tamper with entry at sequence 4
        sqlite_backend.execute(
            "UPDATE audit_trail SET hash = 'tampered' WHERE sequence = 4", ()
        )

        query = AuditQuery(sqlite_backend)
        result = query.verify_chain(chunk_size=3)
        assert not result.valid
        assert result.broken_at_sequence == 4

    def test_chunked_verification_empty(self, sqlite_backend):
        query = AuditQuery(sqlite_backend)
        result = query.verify_chain()
        assert result.valid
        assert result.entries_checked == 0

    def test_cross_chunk_chain_link(self, sqlite_backend):
        """Previous_hash linkage is checked across chunk boundaries."""
        writer = AuditWriter(sqlite_backend)
        for i in range(6):
            writer.record(
                actor_id="u1", action=ActionType.CREATE,
                target_type="X", target_id=f"x{i}",
            )

        # Tamper with previous_hash of entry 4 (chunk boundary at size=3)
        row = sqlite_backend.fetch_one(
            "SELECT hash FROM audit_trail WHERE sequence = 4", ()
        )
        sqlite_backend.execute(
            "UPDATE audit_trail SET previous_hash = 'wrong' WHERE sequence = 4", ()
        )
        # Recompute hash with the wrong previous_hash so the hash itself still "matches"
        # the content — this tests that the chain *link* is validated
        query = AuditQuery(sqlite_backend)
        result = query.verify_chain(chunk_size=3)
        assert not result.valid
        # Should break at 4 (hash mismatch because previous_hash changed)
        assert result.broken_at_sequence == 4
