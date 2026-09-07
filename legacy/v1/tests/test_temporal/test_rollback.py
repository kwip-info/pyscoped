"""Tests for rollback execution."""

from datetime import timedelta

import pytest

from scoped.audit.query import AuditQuery
from scoped.audit.writer import AuditWriter
from scoped.exceptions import RollbackDeniedError, RollbackFailedError
from scoped.identity.principal import PrincipalStore
from scoped.rules.engine import RuleEngine, RuleStore
from scoped.rules.models import BindingTargetType, RuleEffect, RuleType
from scoped.temporal.constraints import RollbackConstraintChecker
from scoped.temporal.rollback import RollbackExecutor, RollbackResult
from scoped.types import ActionType, now_utc


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    return store.create_principal(kind="user", display_name="Alice", principal_id="alice")


@pytest.fixture
def writer(sqlite_backend):
    return AuditWriter(sqlite_backend)


@pytest.fixture
def query(sqlite_backend):
    return AuditQuery(sqlite_backend)


@pytest.fixture
def executor(sqlite_backend, writer):
    return RollbackExecutor(sqlite_backend, audit_writer=writer)


@pytest.fixture
def executor_with_constraints(sqlite_backend, writer):
    checker = RollbackConstraintChecker(sqlite_backend)
    return RollbackExecutor(sqlite_backend, audit_writer=writer, constraint_checker=checker)


@pytest.fixture
def rule_store(sqlite_backend):
    return RuleStore(sqlite_backend)


@pytest.fixture
def rule_engine(sqlite_backend):
    return RuleEngine(sqlite_backend)


# ---- Single-action rollback ----

class TestRollbackAction:

    def test_rollback_single_action(self, executor, writer, query, principals):
        entry = writer.record(
            actor_id="alice", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
            before_state={"version": 1},
            after_state={"version": 2},
        )
        result = executor.rollback_action(entry.id, actor_id="alice")
        assert result.success
        assert entry.id in result.rolled_back
        assert len(result.rollback_trace_ids) == 1

        # Verify rollback trace was created
        rb_trace = query.get(result.rollback_trace_ids[0])
        assert rb_trace.action == ActionType.ROLLBACK
        assert rb_trace.target_id == "obj1"
        assert rb_trace.before_state == {"version": 2}  # swapped
        assert rb_trace.after_state == {"version": 1}    # swapped
        assert rb_trace.metadata["rolled_back_trace_id"] == entry.id

    def test_rollback_nonexistent_trace(self, executor):
        with pytest.raises(RollbackFailedError):
            executor.rollback_action("nonexistent", actor_id="alice")

    def test_rollback_with_reason(self, executor, writer, query, principals):
        entry = writer.record(
            actor_id="alice", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
            before_state={"v": 1}, after_state={"v": 2},
        )
        result = executor.rollback_action(entry.id, actor_id="alice", reason="mistake")
        rb_trace = query.get(result.rollback_trace_ids[0])
        assert rb_trace.metadata["reason"] == "mistake"

    def test_rollback_create_action(self, executor, writer, principals):
        """Rolling back a create where before_state is None."""
        entry = writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
            after_state={"version": 1},
        )
        result = executor.rollback_action(entry.id, actor_id="alice")
        assert result.success

    def test_rollback_denied_by_constraint(
        self, sqlite_backend, writer, principals,
    ):
        checker = RollbackConstraintChecker(sqlite_backend)
        executor = RollbackExecutor(
            sqlite_backend, audit_writer=writer, constraint_checker=checker,
        )
        entry = writer.record(
            actor_id="alice", action=ActionType.READ,
            target_type="audit", target_id="t1",
        )
        with pytest.raises(RollbackDeniedError):
            executor.rollback_action(entry.id, actor_id="alice")


class TestRollbackActionAppliesState:

    def test_restores_object_version(self, sqlite_backend, writer, principals):
        # Create an object directly in the DB
        ts = now_utc().isoformat()
        sqlite_backend.execute(
            """INSERT INTO scoped_objects
               (id, object_type, owner_id, current_version, created_at, lifecycle)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("obj1", "Doc", "alice", 2, ts, "ACTIVE"),
        )
        # Record the update trace with before_state showing version 1
        entry = writer.record(
            actor_id="alice", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
            before_state={"current_version": 1},
            after_state={"current_version": 2},
        )
        executor = RollbackExecutor(sqlite_backend, audit_writer=writer)
        executor.rollback_action(entry.id, actor_id="alice")

        # Check DB was updated
        row = sqlite_backend.fetch_one(
            "SELECT current_version FROM scoped_objects WHERE id = ?", ("obj1",),
        )
        assert row["current_version"] == 1

    def test_archives_object_on_create_rollback(self, sqlite_backend, writer, principals):
        ts = now_utc().isoformat()
        sqlite_backend.execute(
            """INSERT INTO scoped_objects
               (id, object_type, owner_id, current_version, created_at, lifecycle)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("obj1", "Doc", "alice", 1, ts, "ACTIVE"),
        )
        entry = writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
            after_state={"current_version": 1},
        )
        executor = RollbackExecutor(sqlite_backend, audit_writer=writer)
        executor.rollback_action(entry.id, actor_id="alice")

        row = sqlite_backend.fetch_one(
            "SELECT lifecycle FROM scoped_objects WHERE id = ?", ("obj1",),
        )
        assert row["lifecycle"] == "ARCHIVED"


# ---- Point-in-time rollback ----

class TestRollbackToTimestamp:

    def test_rollback_to_timestamp(self, executor, writer, query, principals):
        t_start = now_utc()
        e1 = writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
            after_state={"v": 1},
        )
        t_mid = now_utc()
        e2 = writer.record(
            actor_id="alice", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
            before_state={"v": 1}, after_state={"v": 2},
        )
        e3 = writer.record(
            actor_id="alice", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
            before_state={"v": 2}, after_state={"v": 3},
        )

        result = executor.rollback_to_timestamp(
            "object", "obj1", t_mid,
            actor_id="alice", reason="revert",
        )
        assert result.success
        # e2 and e3 should be rolled back (they occurred after t_mid)
        assert len(result.rolled_back) == 2
        assert e2.id in result.rolled_back
        assert e3.id in result.rolled_back

    def test_nothing_to_rollback(self, executor, writer, principals):
        writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
            after_state={"v": 1},
        )
        result = executor.rollback_to_timestamp(
            "object", "obj1", now_utc(),
            actor_id="alice",
        )
        assert result.success
        assert len(result.rolled_back) == 0

    def test_ignores_other_targets(self, executor, writer, principals):
        t_start = now_utc()
        writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
            after_state={"v": 1},
        )
        writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="object", target_id="obj2",
            after_state={"v": 1},
        )

        result = executor.rollback_to_timestamp(
            "object", "obj1", t_start,
            actor_id="alice",
        )
        # Only obj1 entries should be considered
        for trace_id in result.rolled_back:
            trace = AuditQuery(executor._backend).get(
                AuditQuery(executor._backend).get(
                    # Get the rollback trace to find what was rolled back
                    trace_id
                ).metadata.get("rolled_back_trace_id", trace_id)
            )

    def test_with_denied_entries(
        self, sqlite_backend, writer, rule_store, rule_engine, principals,
    ):
        # Deny rollback in scope s1
        rule = rule_store.create_rule(
            name="No rollback",
            rule_type=RuleType.CONSTRAINT,
            effect=RuleEffect.DENY,
            conditions={"action": ["rollback"]},
            created_by="admin",
        )
        rule_store.bind_rule(
            rule.id, target_type=BindingTargetType.SCOPE,
            target_id="s1", bound_by="admin",
        )

        t_start = now_utc()
        writer.record(
            actor_id="alice", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
            scope_id="s1",
            before_state={"v": 1}, after_state={"v": 2},
        )
        # An entry without scope — should be rollbackable
        writer.record(
            actor_id="alice", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
            before_state={"v": 2}, after_state={"v": 3},
        )

        checker = RollbackConstraintChecker(sqlite_backend, rule_engine=rule_engine)
        executor = RollbackExecutor(
            sqlite_backend, audit_writer=writer, constraint_checker=checker,
        )

        result = executor.rollback_to_timestamp(
            "object", "obj1", t_start, actor_id="alice",
        )
        assert result.success
        assert len(result.denied) == 1
        assert len(result.rolled_back) == 1

    def test_all_denied_raises(
        self, sqlite_backend, writer, principals,
    ):
        checker = RollbackConstraintChecker(sqlite_backend)
        executor = RollbackExecutor(
            sqlite_backend, audit_writer=writer, constraint_checker=checker,
        )

        t_start = now_utc()
        writer.record(
            actor_id="alice", action=ActionType.UPDATE,
            target_type="audit", target_id="t1",
        )

        with pytest.raises(RollbackDeniedError):
            executor.rollback_to_timestamp(
                "audit", "t1", t_start, actor_id="alice",
            )


# ---- Cascading rollback ----

class TestRollbackCascade:

    def test_cascade_rolls_back_children(self, executor, writer, query, principals):
        parent = writer.record(
            actor_id="alice", action=ActionType.SCOPE_CREATE,
            target_type="scope", target_id="s1",
            after_state={"name": "Team"},
        )
        child1 = writer.record(
            actor_id="alice", action=ActionType.MEMBERSHIP_CHANGE,
            target_type="membership", target_id="m1",
            after_state={"scope_id": "s1", "principal_id": "bob"},
            parent_trace_id=parent.id,
        )
        child2 = writer.record(
            actor_id="alice", action=ActionType.PROJECTION,
            target_type="projection", target_id="p1",
            after_state={"scope_id": "s1", "object_id": "obj1"},
            parent_trace_id=parent.id,
        )

        result = executor.rollback_cascade(parent.id, actor_id="alice")
        assert result.success
        # Parent + 2 children
        assert len(result.rolled_back) == 3
        assert parent.id in result.rolled_back
        assert child1.id in result.rolled_back
        assert child2.id in result.rolled_back

    def test_cascade_nonexistent_root(self, executor):
        with pytest.raises(RollbackFailedError):
            executor.rollback_cascade("nonexistent", actor_id="alice")

    def test_cascade_no_children(self, executor, writer, principals):
        entry = writer.record(
            actor_id="alice", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
            before_state={"v": 1}, after_state={"v": 2},
        )
        result = executor.rollback_cascade(entry.id, actor_id="alice")
        assert result.success
        assert len(result.rolled_back) == 1
        assert entry.id in result.rolled_back

    def test_cascade_deep_hierarchy(self, executor, writer, principals):
        root = writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
            after_state={"v": 1},
        )
        child = writer.record(
            actor_id="alice", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
            after_state={"v": 2},
            parent_trace_id=root.id,
        )
        grandchild = writer.record(
            actor_id="alice", action=ActionType.UPDATE,
            target_type="object", target_id="obj1",
            after_state={"v": 3},
            parent_trace_id=child.id,
        )

        result = executor.rollback_cascade(root.id, actor_id="alice")
        assert result.success
        assert len(result.rolled_back) == 3

    def test_cascade_with_denied_child(
        self, sqlite_backend, writer, principals,
    ):
        checker = RollbackConstraintChecker(sqlite_backend)
        executor = RollbackExecutor(
            sqlite_backend, audit_writer=writer, constraint_checker=checker,
        )

        root = writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="object", target_id="obj1",
            after_state={"v": 1},
        )
        # Child targets audit — immutable, cannot be rolled back
        child = writer.record(
            actor_id="alice", action=ActionType.READ,
            target_type="audit", target_id="t1",
            parent_trace_id=root.id,
        )

        result = executor.rollback_cascade(root.id, actor_id="alice")
        assert result.success
        assert root.id in result.rolled_back
        assert child.id in result.denied

    def test_cascade_reverse_chronological_order(self, executor, writer, query, principals):
        """Children are rolled back before their parent."""
        root = writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="scope", target_id="s1",
            after_state={"name": "S"},
        )
        child = writer.record(
            actor_id="alice", action=ActionType.MEMBERSHIP_CHANGE,
            target_type="membership", target_id="m1",
            after_state={"scope": "s1"},
            parent_trace_id=root.id,
        )

        result = executor.rollback_cascade(root.id, actor_id="alice")

        # Verify order: child trace was rolled back first
        rb_traces = [query.get(tid) for tid in result.rollback_trace_ids]
        child_rb = next(
            t for t in rb_traces
            if t.metadata["rolled_back_trace_id"] == child.id
        )
        root_rb = next(
            t for t in rb_traces
            if t.metadata["rolled_back_trace_id"] == root.id
        )
        assert child_rb.sequence < root_rb.sequence


# ---- Environment rollback ----

class TestRollbackEnvironment:

    def test_rollback_restores_environment_state(self, sqlite_backend, writer, principals):
        """Rolling back an env transition restores the previous state."""
        ts = now_utc().isoformat()
        sqlite_backend.execute(
            """INSERT INTO environments
               (id, name, owner_id, state, created_at, description, ephemeral, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("env1", "Test", "alice", "suspended", ts, "", 1, "{}"),
        )
        entry = writer.record(
            actor_id="alice", action=ActionType.ENV_SUSPEND,
            target_type="environment", target_id="env1",
            before_state={"state": "active"},
            after_state={"state": "suspended"},
        )
        executor = RollbackExecutor(sqlite_backend, audit_writer=writer)
        result = executor.rollback_action(entry.id, actor_id="alice")
        assert result.success

        row = sqlite_backend.fetch_one(
            "SELECT state FROM environments WHERE id = ?", ("env1",),
        )
        assert row["state"] == "active"

    def test_rollback_env_create_marks_discarded(self, sqlite_backend, writer, principals):
        """Rolling back an env spawn marks it discarded."""
        ts = now_utc().isoformat()
        sqlite_backend.execute(
            """INSERT INTO environments
               (id, name, owner_id, state, created_at, description, ephemeral, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("env2", "Test", "alice", "spawning", ts, "", 1, "{}"),
        )
        entry = writer.record(
            actor_id="alice", action=ActionType.ENV_SPAWN,
            target_type="environment", target_id="env2",
            after_state={"state": "spawning"},
        )
        executor = RollbackExecutor(sqlite_backend, audit_writer=writer)
        result = executor.rollback_action(entry.id, actor_id="alice")
        assert result.success

        row = sqlite_backend.fetch_one(
            "SELECT state FROM environments WHERE id = ?", ("env2",),
        )
        assert row["state"] == "discarded"


# ---- RollbackResult ----

class TestRollbackResult:

    def test_bool_success(self):
        r = RollbackResult(
            success=True, rolled_back=("t1",), rollback_trace_ids=("r1",),
        )
        assert bool(r) is True

    def test_bool_failure(self):
        r = RollbackResult(
            success=False, rolled_back=(), rollback_trace_ids=(),
        )
        assert bool(r) is False

    def test_repr(self):
        r = RollbackResult(
            success=True, rolled_back=("t1", "t2"),
            rollback_trace_ids=("r1", "r2"), denied=("d1",),
        )
        s = repr(r)
        assert "rolled_back=2" in s
        assert "denied=1" in s
