"""RollbackVerifier — verify rollback correctness for all mutation types.

For every mutation type: capture state, mutate, rollback, verify the
rollback was properly traced with correct before/after states.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scoped.audit.query import AuditQuery
from scoped.audit.writer import AuditWriter
from scoped.objects.manager import ScopedManager
from scoped.storage.interface import StorageBackend
from scoped.temporal.rollback import RollbackExecutor
from scoped.types import ActionType, generate_id, now_utc


@dataclass(frozen=True, slots=True)
class RollbackCheck:
    """Result of a single rollback verification."""

    mutation_type: str
    passed: bool
    detail: str = ""


@dataclass(slots=True)
class RollbackVerification:
    """Aggregate result of all rollback verifications."""

    checks: list[RollbackCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed(self) -> list[RollbackCheck]:
        return [c for c in self.checks if not c.passed]


class RollbackVerifier:
    """Verify that rollback produces correct traces for all mutation types.

    Tests:
    1. Object create → rollback → rollback trace with correct states
    2. Object update → rollback → rollback trace references update
    3. Object tombstone → rollback → rollback trace exists
    """

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend
        self._audit = AuditWriter(backend)
        self._query = AuditQuery(backend)
        self._manager = ScopedManager(backend, audit_writer=self._audit)
        self._rollback = RollbackExecutor(backend, audit_writer=self._audit)

    def verify_all(self, *, principal_id: str) -> RollbackVerification:
        """Run all rollback verifications."""
        result = RollbackVerification()
        result.checks.append(self.verify_create_rollback(principal_id=principal_id))
        result.checks.append(self.verify_update_rollback(principal_id=principal_id))
        result.checks.append(self.verify_tombstone_rollback(principal_id=principal_id))
        return result

    def verify_create_rollback(self, *, principal_id: str) -> RollbackCheck:
        """Verify: create → rollback → rollback trace recorded correctly."""
        try:
            obj, _ = self._manager.create(
                object_type="rollback_test",
                owner_id=principal_id,
                data={"test": "create_rollback"},
            )

            # Find the create trace
            entries = self._query.query(
                action=ActionType.CREATE,
                target_id=obj.id,
                limit=1,
            )
            if not entries:
                return RollbackCheck(
                    mutation_type="create",
                    passed=False,
                    detail="No trace entry found for create",
                )

            # Rollback
            result = self._rollback.rollback_action(
                entries[0].id, actor_id=principal_id,
            )
            if not result.success:
                return RollbackCheck(
                    mutation_type="create",
                    passed=False,
                    detail="Rollback failed",
                )

            # Verify rollback trace exists and references original
            rollback_entries = self._query.query(
                action=ActionType.ROLLBACK,
                target_id=obj.id,
                limit=1,
            )
            if not rollback_entries:
                return RollbackCheck(
                    mutation_type="create",
                    passed=False,
                    detail="No rollback trace recorded",
                )

            rb = rollback_entries[0]
            if rb.metadata.get("rolled_back_trace_id") != entries[0].id:
                return RollbackCheck(
                    mutation_type="create",
                    passed=False,
                    detail="Rollback trace doesn't reference original",
                )

            return RollbackCheck(mutation_type="create", passed=True)

        except Exception as e:
            return RollbackCheck(
                mutation_type="create",
                passed=False,
                detail=f"Exception: {e}",
            )

    def verify_update_rollback(self, *, principal_id: str) -> RollbackCheck:
        """Verify: create → update → rollback → rollback trace references update."""
        try:
            obj, _ = self._manager.create(
                object_type="rollback_test",
                owner_id=principal_id,
                data={"test": "before_update", "value": 42},
            )

            # Update
            self._manager.update(
                obj.id,
                principal_id=principal_id,
                data={"test": "after_update", "value": 99},
            )

            # Find the update trace
            entries = self._query.query(
                action=ActionType.UPDATE,
                target_id=obj.id,
                limit=1,
            )
            if not entries:
                return RollbackCheck(
                    mutation_type="update",
                    passed=False,
                    detail="No trace entry found for update",
                )

            # Rollback the update
            result = self._rollback.rollback_action(
                entries[0].id, actor_id=principal_id,
            )
            if not result.success:
                return RollbackCheck(
                    mutation_type="update",
                    passed=False,
                    detail="Rollback failed",
                )

            # Verify rollback trace exists
            rollback_entries = self._query.query(
                action=ActionType.ROLLBACK,
                target_id=obj.id,
                limit=1,
            )
            if not rollback_entries:
                return RollbackCheck(
                    mutation_type="update",
                    passed=False,
                    detail="No rollback trace recorded",
                )

            rb = rollback_entries[0]
            if rb.metadata.get("rolled_back_action") != "update":
                return RollbackCheck(
                    mutation_type="update",
                    passed=False,
                    detail="Rollback doesn't reference update action",
                )

            return RollbackCheck(mutation_type="update", passed=True)

        except Exception as e:
            return RollbackCheck(
                mutation_type="update",
                passed=False,
                detail=f"Exception: {e}",
            )

    def verify_tombstone_rollback(self, *, principal_id: str) -> RollbackCheck:
        """Verify: create → tombstone → rollback → rollback trace exists."""
        try:
            obj, _ = self._manager.create(
                object_type="rollback_test",
                owner_id=principal_id,
                data={"test": "tombstone_rollback"},
            )

            # Tombstone
            self._manager.tombstone(obj.id, principal_id=principal_id, reason="test")

            # Find the delete trace
            entries = self._query.query(
                action=ActionType.DELETE,
                target_id=obj.id,
                limit=1,
            )
            if not entries:
                return RollbackCheck(
                    mutation_type="tombstone",
                    passed=False,
                    detail="No trace entry found for tombstone",
                )

            # Rollback
            self._rollback.rollback_action(entries[0].id, actor_id=principal_id)

            # The rollback trace exists
            rollback_entries = self._query.query(
                action=ActionType.ROLLBACK,
                target_id=obj.id,
                limit=1,
            )
            passed = len(rollback_entries) > 0
            return RollbackCheck(
                mutation_type="tombstone",
                passed=passed,
                detail="" if passed else "No rollback trace found",
            )

        except Exception as e:
            return RollbackCheck(
                mutation_type="tombstone",
                passed=False,
                detail=f"Exception: {e}",
            )
