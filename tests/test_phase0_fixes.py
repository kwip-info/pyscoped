"""Tests for Phase 0 fixes: hierarchy CTEs, indexes, thread-safe init, audit sequence safety."""

import threading

import pytest

from scoped.audit.writer import AuditWriter
from scoped.client import ScopedClient, _default_client_lock, init
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.tenancy.engine import VisibilityEngine
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.tenancy.models import ScopeRole
from scoped.tenancy.projection import ProjectionManager
from scoped.types import ActionType


# -- Fixtures -----------------------------------------------------------------

@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    bob = store.create_principal(kind="user", display_name="Bob", principal_id="bob")
    carol = store.create_principal(kind="user", display_name="Carol", principal_id="carol")
    return alice, bob, carol


@pytest.fixture
def lc(sqlite_backend):
    return ScopeLifecycle(sqlite_backend)


@pytest.fixture
def mgr(sqlite_backend):
    return ScopedManager(sqlite_backend)


@pytest.fixture
def proj(sqlite_backend):
    return ProjectionManager(sqlite_backend)


@pytest.fixture
def engine(sqlite_backend):
    return VisibilityEngine(sqlite_backend)


# =============================================================================
# 1. Recursive CTE hierarchy traversal
# =============================================================================


class TestRecursiveCTEAncestors:
    """Verify ancestor_scope_ids returns correct results via recursive CTE."""

    def test_no_parent_returns_empty(self, lc, engine, principals):
        alice, _, _ = principals
        root = lc.create_scope(name="Root", owner_id=alice.id)
        assert engine.ancestor_scope_ids(root.id) == []

    def test_single_parent(self, lc, engine, principals):
        alice, _, _ = principals
        parent = lc.create_scope(name="Parent", owner_id=alice.id)
        child = lc.create_scope(name="Child", owner_id=alice.id, parent_scope_id=parent.id)
        assert engine.ancestor_scope_ids(child.id) == [parent.id]

    def test_deep_hierarchy_single_query(self, lc, engine, principals):
        """Build a 5-level deep hierarchy and verify all ancestors are returned."""
        alice, _, _ = principals
        scopes = []
        parent_id = None
        for i in range(5):
            s = lc.create_scope(name=f"L{i}", owner_id=alice.id, parent_scope_id=parent_id)
            scopes.append(s)
            parent_id = s.id

        # Leaf is scopes[4], ancestors should be [scopes[3], scopes[2], scopes[1], scopes[0]]
        ancestors = engine.ancestor_scope_ids(scopes[4].id)
        expected = [scopes[3].id, scopes[2].id, scopes[1].id, scopes[0].id]
        assert ancestors == expected

    def test_max_depth_respected(self, lc, engine, principals):
        alice, _, _ = principals
        scopes = []
        parent_id = None
        for i in range(5):
            s = lc.create_scope(name=f"L{i}", owner_id=alice.id, parent_scope_id=parent_id)
            scopes.append(s)
            parent_id = s.id

        ancestors = engine.ancestor_scope_ids(scopes[4].id, max_depth=2)
        # Should only go 2 levels up: scopes[3], scopes[2]
        assert len(ancestors) == 2
        assert ancestors == [scopes[3].id, scopes[2].id]

    def test_nonexistent_scope_returns_empty(self, engine):
        assert engine.ancestor_scope_ids("nonexistent") == []


class TestRecursiveCTEDescendants:
    """Verify descendant_scope_ids returns correct results via recursive CTE."""

    def test_no_children_returns_empty(self, lc, engine, principals):
        alice, _, _ = principals
        leaf = lc.create_scope(name="Leaf", owner_id=alice.id)
        assert engine.descendant_scope_ids(leaf.id) == []

    def test_wide_tree(self, lc, engine, principals):
        alice, _, _ = principals
        root = lc.create_scope(name="Root", owner_id=alice.id)
        c1 = lc.create_scope(name="C1", owner_id=alice.id, parent_scope_id=root.id)
        c2 = lc.create_scope(name="C2", owner_id=alice.id, parent_scope_id=root.id)
        c3 = lc.create_scope(name="C3", owner_id=alice.id, parent_scope_id=root.id)

        descendants = engine.descendant_scope_ids(root.id)
        assert set(descendants) == {c1.id, c2.id, c3.id}

    def test_deep_tree(self, lc, engine, principals):
        alice, _, _ = principals
        scopes = []
        parent_id = None
        for i in range(5):
            s = lc.create_scope(name=f"D{i}", owner_id=alice.id, parent_scope_id=parent_id)
            scopes.append(s)
            parent_id = s.id

        descendants = engine.descendant_scope_ids(scopes[0].id)
        expected = {scopes[1].id, scopes[2].id, scopes[3].id, scopes[4].id}
        assert set(descendants) == expected

    def test_archived_excluded(self, lc, engine, principals):
        alice, _, _ = principals
        root = lc.create_scope(name="Root", owner_id=alice.id)
        active = lc.create_scope(name="Active", owner_id=alice.id, parent_scope_id=root.id)
        archived = lc.create_scope(name="Archived", owner_id=alice.id, parent_scope_id=root.id)
        lc.archive_scope(archived.id, archived_by=alice.id)

        descendants = engine.descendant_scope_ids(root.id)
        assert active.id in descendants
        assert archived.id not in descendants


class TestHierarchyVisibilityCTE:
    """Verify _visible_via_hierarchy works correctly with the CTE rewrite."""

    def test_child_member_sees_parent_projection(self, mgr, lc, proj, engine, principals):
        alice, bob, _ = principals
        parent = lc.create_scope(name="Org", owner_id=alice.id)
        child = lc.create_scope(name="Team", owner_id=alice.id, parent_scope_id=parent.id)
        lc.add_member(child.id, principal_id=bob.id, granted_by=alice.id)

        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={"x": 1})
        proj.project(scope_id=parent.id, object_id=obj.id, projected_by=alice.id)

        assert engine.can_see(bob.id, obj.id)

    def test_grandchild_member_sees_grandparent_projection(self, mgr, lc, proj, engine, principals):
        """3-level hierarchy: grandchild member should see grandparent projection."""
        alice, bob, _ = principals
        grandparent = lc.create_scope(name="Corp", owner_id=alice.id)
        parent = lc.create_scope(name="Div", owner_id=alice.id, parent_scope_id=grandparent.id)
        child = lc.create_scope(name="Team", owner_id=alice.id, parent_scope_id=parent.id)
        lc.add_member(child.id, principal_id=bob.id, granted_by=alice.id)

        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={"x": 1})
        proj.project(scope_id=grandparent.id, object_id=obj.id, projected_by=alice.id)

        assert engine.can_see(bob.id, obj.id)

    def test_non_member_cannot_see_via_hierarchy(self, mgr, lc, proj, engine, principals):
        alice, bob, carol = principals
        parent = lc.create_scope(name="Org", owner_id=alice.id)
        child = lc.create_scope(name="Team", owner_id=alice.id, parent_scope_id=parent.id)
        lc.add_member(child.id, principal_id=bob.id, granted_by=alice.id)

        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={"x": 1})
        proj.project(scope_id=parent.id, object_id=obj.id, projected_by=alice.id)

        # Carol is NOT a member of child — should not see it
        assert not engine.can_see(carol.id, obj.id)

    def test_direct_member_does_not_use_hierarchy(self, mgr, lc, proj, engine, principals):
        """Direct scope member seeing a direct projection doesn't need hierarchy."""
        alice, bob, _ = principals
        scope = lc.create_scope(name="Team", owner_id=alice.id)
        lc.add_member(scope.id, principal_id=bob.id, granted_by=alice.id)

        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={"x": 1})
        proj.project(scope_id=scope.id, object_id=obj.id, projected_by=alice.id)

        # Should be visible via direct projection (not hierarchy)
        assert engine.can_see(bob.id, obj.id)

    def test_multiple_memberships_hierarchy(self, mgr, lc, proj, engine, principals):
        """Principal in multiple scopes — hierarchy should cover all paths."""
        alice, bob, _ = principals
        org = lc.create_scope(name="Org", owner_id=alice.id)
        team_a = lc.create_scope(name="TeamA", owner_id=alice.id, parent_scope_id=org.id)
        team_b = lc.create_scope(name="TeamB", owner_id=alice.id, parent_scope_id=org.id)
        lc.add_member(team_a.id, principal_id=bob.id, granted_by=alice.id)
        lc.add_member(team_b.id, principal_id=bob.id, granted_by=alice.id)

        obj, _ = mgr.create(object_type="Doc", owner_id=alice.id, data={"x": 1})
        proj.project(scope_id=org.id, object_id=obj.id, projected_by=alice.id)

        assert engine.can_see(bob.id, obj.id)


# =============================================================================
# 2. Composite indexes migration
# =============================================================================


class TestCompositeIndexes:
    """Verify migration m0012 creates the expected indexes."""

    def test_indexes_exist_after_init(self, sqlite_backend):
        """SQLiteBackend.initialize() runs all migrations; verify indexes exist."""
        # Query sqlite_master for our new indexes
        rows = sqlite_backend.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%'",
            (),
        )
        index_names = {r["name"] for r in rows}

        assert "idx_projections_scope_lifecycle" in index_names
        assert "idx_memberships_scope_lifecycle" in index_names
        assert "idx_memberships_principal_lifecycle" in index_names
        assert "idx_audit_action_timestamp" in index_names


# =============================================================================
# 3. Thread-safe global client init
# =============================================================================


class TestThreadSafeInit:
    """Verify scoped.init() is safe under concurrent calls."""

    @pytest.fixture(autouse=True)
    def _reset_global(self):
        import scoped.client
        original = scoped.client._default_client
        yield
        scoped.client._default_client = original

    def test_concurrent_init_no_crash(self):
        """Multiple threads calling init() should not crash or lose the client."""
        clients = []
        errors = []

        def do_init():
            try:
                c = init()
                clients.append(c)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=do_init) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent init: {errors}"
        assert len(clients) == 10
        # All should have produced valid clients
        for c in clients:
            assert c is not None
            c.close()

    def test_final_client_is_usable(self):
        """After concurrent init, the global client should be functional."""
        results = []

        def do_init():
            c = init()
            results.append(c)

        threads = [threading.Thread(target=do_init) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        import scoped.client
        client = scoped.client._default_client
        assert client is not None
        # Should be one of the clients that was created
        assert client in results
        for c in results:
            c.close()


# =============================================================================
# 4. Multi-process audit sequence safety (simulated via two writers)
# =============================================================================


class TestAuditSequenceReseed:
    """Verify that a writer re-seeds from DB when another writer has advanced the sequence."""

    def test_second_writer_continues_sequence(self, sqlite_backend):
        """Two writers on the same backend don't collide on sequence numbers."""
        w1 = AuditWriter(sqlite_backend)
        w2 = AuditWriter(sqlite_backend)

        e1 = w1.record(
            actor_id="u1", action=ActionType.CREATE,
            target_type="X", target_id="x1",
        )
        assert e1.sequence == 1

        # w2 was initialized before w1 wrote, so it thinks sequence is 0.
        # After the fix, _reseed_if_stale() should pick up sequence=1 from DB.
        e2 = w2.record(
            actor_id="u2", action=ActionType.CREATE,
            target_type="Y", target_id="y1",
        )
        assert e2.sequence == 2, (
            f"Expected sequence 2 (reseeded), got {e2.sequence}"
        )
        assert e2.previous_hash == e1.hash

    def test_interleaved_writes_maintain_chain(self, sqlite_backend):
        """Alternating writes between two writers should produce a valid chain."""
        w1 = AuditWriter(sqlite_backend)
        w2 = AuditWriter(sqlite_backend)

        e1 = w1.record(
            actor_id="u1", action=ActionType.CREATE,
            target_type="A", target_id="a1",
        )
        e2 = w2.record(
            actor_id="u2", action=ActionType.CREATE,
            target_type="B", target_id="b1",
        )
        e3 = w1.record(
            actor_id="u1", action=ActionType.UPDATE,
            target_type="A", target_id="a1",
        )
        e4 = w2.record(
            actor_id="u2", action=ActionType.UPDATE,
            target_type="B", target_id="b1",
        )

        assert e1.sequence == 1
        assert e2.sequence == 2
        assert e3.sequence == 3
        assert e4.sequence == 4

        # Verify chain integrity
        assert e2.previous_hash == e1.hash
        assert e3.previous_hash == e2.hash
        assert e4.previous_hash == e3.hash

    def test_batch_reseeds_before_writing(self, sqlite_backend):
        """record_batch should also reseed from DB before writing."""
        w1 = AuditWriter(sqlite_backend)
        w2 = AuditWriter(sqlite_backend)

        e1 = w1.record(
            actor_id="u1", action=ActionType.CREATE,
            target_type="X", target_id="x1",
        )

        entries = w2.record_batch([
            {"actor_id": "u2", "action": ActionType.CREATE, "target_type": "A", "target_id": "a1"},
            {"actor_id": "u2", "action": ActionType.CREATE, "target_type": "B", "target_id": "b1"},
        ])

        assert entries[0].sequence == 2
        assert entries[1].sequence == 3
        assert entries[0].previous_hash == e1.hash

    def test_single_writer_unchanged(self, sqlite_backend):
        """Single writer still works correctly — reseed is a no-op when not stale."""
        writer = AuditWriter(sqlite_backend)

        e1 = writer.record(
            actor_id="u1", action=ActionType.CREATE,
            target_type="X", target_id="x1",
        )
        e2 = writer.record(
            actor_id="u1", action=ActionType.UPDATE,
            target_type="X", target_id="x1",
        )
        e3 = writer.record(
            actor_id="u1", action=ActionType.DELETE,
            target_type="X", target_id="x1",
        )

        assert e1.sequence == 1
        assert e2.sequence == 2
        assert e3.sequence == 3
        assert e2.previous_hash == e1.hash
        assert e3.previous_hash == e2.hash

    def test_chain_verification_after_interleaved_writes(self, sqlite_backend):
        """Full chain verification should pass after interleaved multi-writer scenario."""
        from scoped.audit.query import AuditQuery

        w1 = AuditWriter(sqlite_backend)
        w2 = AuditWriter(sqlite_backend)

        w1.record(actor_id="u1", action=ActionType.CREATE, target_type="X", target_id="x1")
        w2.record(actor_id="u2", action=ActionType.CREATE, target_type="Y", target_id="y1")
        w1.record(actor_id="u1", action=ActionType.UPDATE, target_type="X", target_id="x1")
        w2.record(actor_id="u2", action=ActionType.UPDATE, target_type="Y", target_id="y1")

        query = AuditQuery(sqlite_backend)
        verification = query.verify_chain()
        assert verification.valid, f"Chain broken at sequence {verification.broken_at_sequence}"
        assert verification.entries_checked == 4
