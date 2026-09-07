"""Rollback constraint checking via the rule engine.

Before executing a rollback, constraints are evaluated to determine
whether the rollback is permitted.  Rules of type CONSTRAINT with
action ``rollback`` (or more specific actions like ``rollback:object``)
can block rollback operations.

Hard constraints (audit trail immutability) are enforced here as well —
audit trail entries can never be rolled back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scoped.audit.models import TraceEntry
from scoped.exceptions import RollbackDeniedError
from scoped.rules.engine import RuleEngine
from scoped.storage.interface import StorageBackend


@dataclass(frozen=True, slots=True)
class ConstraintCheck:
    """Result of a rollback constraint check."""

    permitted: bool
    reason: str
    trace_entry: TraceEntry
    deny_rules: tuple[Any, ...] = ()

    def __bool__(self) -> bool:
        return self.permitted


class RollbackConstraintChecker:
    """Check whether a rollback is permitted by rules and hard constraints.

    Hard constraints:
    - Audit trail entries (target_type ``audit``) cannot be rolled back.

    Soft constraints (via rule engine):
    - Rules with action ``rollback`` matching the target's scope, type,
      or principal can deny rollback.
    """

    # Target types that can never be rolled back.
    IMMUTABLE_TARGET_TYPES = frozenset({"audit", "trace"})

    def __init__(
        self,
        backend: StorageBackend,
        *,
        rule_engine: RuleEngine | None = None,
    ) -> None:
        self._backend = backend
        self._rule_engine = rule_engine

    def check(
        self,
        trace_entry: TraceEntry,
        *,
        actor_id: str,
        principal_kind: str | None = None,
    ) -> ConstraintCheck:
        """Check whether rolling back *trace_entry* is permitted.

        Returns a :class:`ConstraintCheck`.  If not permitted, the
        ``reason`` field explains why.
        """
        # Hard constraint: immutable target types
        if trace_entry.target_type in self.IMMUTABLE_TARGET_TYPES:
            return ConstraintCheck(
                permitted=False,
                reason=f"Target type '{trace_entry.target_type}' is immutable and cannot be rolled back",
                trace_entry=trace_entry,
            )

        # Soft constraints: ask the rule engine
        if self._rule_engine is not None:
            result = self._rule_engine.evaluate(
                action="rollback",
                principal_id=actor_id,
                principal_kind=principal_kind,
                object_type=trace_entry.target_type,
                object_id=trace_entry.target_id,
                scope_id=trace_entry.scope_id,
            )
            if not result.allowed and result.deny_rules:
                return ConstraintCheck(
                    permitted=False,
                    reason="Rollback denied by rule engine",
                    trace_entry=trace_entry,
                    deny_rules=result.deny_rules,
                )

        return ConstraintCheck(
            permitted=True,
            reason="Rollback permitted",
            trace_entry=trace_entry,
        )

    def check_or_raise(
        self,
        trace_entry: TraceEntry,
        *,
        actor_id: str,
        principal_kind: str | None = None,
    ) -> ConstraintCheck:
        """Like :meth:`check` but raises :class:`RollbackDeniedError`
        if the rollback is not permitted.
        """
        result = self.check(
            trace_entry,
            actor_id=actor_id,
            principal_kind=principal_kind,
        )
        if not result.permitted:
            raise RollbackDeniedError(
                result.reason,
                context={
                    "trace_id": trace_entry.id,
                    "target_type": trace_entry.target_type,
                    "target_id": trace_entry.target_id,
                    "actor_id": actor_id,
                },
            )
        return result

    def check_many(
        self,
        trace_entries: list[TraceEntry],
        *,
        actor_id: str,
        principal_kind: str | None = None,
    ) -> list[ConstraintCheck]:
        """Check constraints for multiple trace entries.

        Returns a list of :class:`ConstraintCheck` in the same order.
        Does NOT short-circuit — all entries are checked so the caller
        can report all failures at once.
        """
        return [
            self.check(entry, actor_id=actor_id, principal_kind=principal_kind)
            for entry in trace_entries
        ]
