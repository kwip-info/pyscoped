"""Rollback execution: single-action, point-in-time, and cascading.

Every rollback is itself a traced action.  Rolling back an action
restores the ``before_state`` of the original trace entry.  Cascading
rollback walks the ``parent_trace_id`` dependency chain and rolls back
all downstream actions in reverse chronological order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from scoped.audit.models import TraceEntry
from scoped.audit.query import AuditQuery
from scoped.audit.writer import AuditWriter
from scoped.exceptions import RollbackDeniedError, RollbackFailedError
from scoped.storage._query import compile_for
from scoped.storage._schema import environments, scoped_objects
from scoped.storage.interface import StorageBackend
from scoped.temporal.constraints import RollbackConstraintChecker
from scoped.types import ActionType


@dataclass(frozen=True, slots=True)
class RollbackResult:
    """Outcome of a rollback operation."""

    success: bool
    rolled_back: tuple[str, ...]
    """Trace IDs that were rolled back."""
    rollback_trace_ids: tuple[str, ...]
    """Trace IDs of the rollback entries created."""
    skipped: tuple[str, ...] = ()
    """Trace IDs that were skipped (already rolled back, etc.)."""
    denied: tuple[str, ...] = ()
    """Trace IDs whose rollback was denied by constraints."""

    def __bool__(self) -> bool:
        return self.success

    def __repr__(self) -> str:
        return (
            f"RollbackResult(success={self.success}, "
            f"rolled_back={len(self.rolled_back)}, "
            f"skipped={len(self.skipped)}, "
            f"denied={len(self.denied)})"
        )


@dataclass(frozen=True, slots=True)
class RollbackPreview:
    """Preview of what a rollback would do (dry-run result).

    Returned when ``dry_run=True`` is passed to any rollback method.
    No database changes are made.
    """

    would_rollback: tuple[str, ...]
    """Trace IDs that would be rolled back."""
    would_skip: tuple[str, ...] = ()
    """Trace IDs that would be skipped (already rolled back, etc.)."""
    would_deny: tuple[str, ...] = ()
    """Trace IDs whose rollback would be denied by constraints."""
    entry_count: int = 0
    """Total number of entries in scope."""

    def __repr__(self) -> str:
        return (
            f"RollbackPreview(rollback={len(self.would_rollback)}, "
            f"skip={len(self.would_skip)}, deny={len(self.would_deny)})"
        )


class RollbackExecutor:
    """Execute rollback operations with constraint checking and tracing.

    Supports three rollback modes:

    1. **Single-action** — reverse a specific traced action.
    2. **Point-in-time** — restore a target to its state at a timestamp.
    3. **Cascading** — rollback an action and all dependent actions.
    """

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: AuditWriter,
        constraint_checker: RollbackConstraintChecker | None = None,
    ) -> None:
        self._backend = backend
        self._writer = audit_writer
        self._query = AuditQuery(backend)
        self._constraints = constraint_checker

    # -----------------------------------------------------------------
    # Single-action rollback
    # -----------------------------------------------------------------

    def rollback_action(
        self,
        trace_id: str,
        *,
        actor_id: str,
        principal_kind: str | None = None,
        reason: str = "",
        dry_run: bool = False,
    ) -> RollbackResult | RollbackPreview:
        """Roll back a single traced action.

        Restores the ``before_state`` of the trace entry and records
        a rollback trace.  If the entry has no ``before_state`` (e.g.
        a create action), the target is marked as rolled back with
        a ``None`` state.

        When ``dry_run=True``, returns a :class:`RollbackPreview`
        without modifying any data.

        Raises :class:`RollbackFailedError` if the trace entry
        does not exist.  Raises :class:`RollbackDeniedError` if
        constraints block the rollback.
        """
        entry = self._query.get(trace_id)
        if entry is None:
            raise RollbackFailedError(
                f"Trace entry '{trace_id}' not found",
                context={"trace_id": trace_id},
            )

        # Check constraints
        self._check_constraints(entry, actor_id=actor_id, principal_kind=principal_kind)

        if dry_run:
            return RollbackPreview(
                would_rollback=(entry.id,),
                entry_count=1,
            )

        # Execute
        rollback_entry = self._execute_single_rollback(
            entry, actor_id=actor_id, reason=reason,
        )

        return RollbackResult(
            success=True,
            rolled_back=(entry.id,),
            rollback_trace_ids=(rollback_entry.id,),
        )

    # -----------------------------------------------------------------
    # Point-in-time rollback
    # -----------------------------------------------------------------

    def rollback_to_timestamp(
        self,
        target_type: str,
        target_id: str,
        at: datetime,
        *,
        actor_id: str,
        principal_kind: str | None = None,
        reason: str = "",
        dry_run: bool = False,
    ) -> RollbackResult | RollbackPreview:
        """Restore a target to its state at timestamp *at*.

        Finds all trace entries for the target after *at* and rolls
        them back in reverse chronological order.
        """
        # Find all entries after the timestamp
        entries = self._query.query(
            target_type=target_type,
            target_id=target_id,
            since=at,
            limit=10000,
        )

        # Filter to entries strictly after the timestamp
        entries = [e for e in entries if e.timestamp > at]

        if not entries:
            return RollbackResult(
                success=True,
                rolled_back=(),
                rollback_trace_ids=(),
            )

        # Reverse chronological order
        entries.sort(key=lambda e: e.sequence, reverse=True)

        # Check constraints for all
        denied: list[str] = []
        permitted: list[TraceEntry] = []
        for entry in entries:
            if self._constraints is not None:
                check = self._constraints.check(
                    entry, actor_id=actor_id, principal_kind=principal_kind,
                )
                if not check.permitted:
                    denied.append(entry.id)
                    continue
            permitted.append(entry)

        if denied and not permitted:
            raise RollbackDeniedError(
                "All actions in the rollback range are denied by constraints",
                context={
                    "target_type": target_type,
                    "target_id": target_id,
                    "denied_count": len(denied),
                },
            )

        if dry_run:
            return RollbackPreview(
                would_rollback=tuple(e.id for e in permitted),
                would_deny=tuple(denied),
                entry_count=len(entries),
            )

        # Execute rollbacks
        rolled_back: list[str] = []
        rollback_traces: list[str] = []
        for entry in permitted:
            rb_entry = self._execute_single_rollback(
                entry, actor_id=actor_id, reason=reason or f"Point-in-time rollback to {at.isoformat()}",
            )
            rolled_back.append(entry.id)
            rollback_traces.append(rb_entry.id)

        return RollbackResult(
            success=True,
            rolled_back=tuple(rolled_back),
            rollback_trace_ids=tuple(rollback_traces),
            denied=tuple(denied),
        )

    # -----------------------------------------------------------------
    # Cascading rollback
    # -----------------------------------------------------------------

    def rollback_cascade(
        self,
        trace_id: str,
        *,
        actor_id: str,
        principal_kind: str | None = None,
        reason: str = "",
        dry_run: bool = False,
    ) -> RollbackResult | RollbackPreview:
        """Roll back an action and all actions that depend on it.

        Walks the ``parent_trace_id`` chain to find all downstream
        actions, then rolls them back in reverse chronological order
        (children first, then the root).

        If any downstream action is denied by constraints, it is
        skipped and reported in the result.
        """
        root = self._query.get(trace_id)
        if root is None:
            raise RollbackFailedError(
                f"Trace entry '{trace_id}' not found",
                context={"trace_id": trace_id},
            )

        # Check root constraint
        self._check_constraints(root, actor_id=actor_id, principal_kind=principal_kind)

        # Collect all descendants via BFS
        all_entries = self._collect_descendants(trace_id)
        # Include the root
        all_entries.append(root)

        # Reverse chronological order (children before parents)
        all_entries.sort(key=lambda e: e.sequence, reverse=True)

        # Deduplicate (root is in both BFS result and explicit append)
        seen: set[str] = set()
        unique: list[TraceEntry] = []
        for entry in all_entries:
            if entry.id not in seen:
                seen.add(entry.id)
                unique.append(entry)
        all_entries = unique

        # Check constraints
        denied: list[str] = []
        permitted: list[TraceEntry] = []
        for entry in all_entries:
            if entry.id != root.id and self._constraints is not None:
                check = self._constraints.check(
                    entry, actor_id=actor_id, principal_kind=principal_kind,
                )
                if not check.permitted:
                    denied.append(entry.id)
                    continue
            permitted.append(entry)

        if dry_run:
            return RollbackPreview(
                would_rollback=tuple(e.id for e in permitted),
                would_deny=tuple(denied),
                entry_count=len(all_entries),
            )

        # Execute
        rolled_back: list[str] = []
        rollback_traces: list[str] = []

        for entry in permitted:
            rb_entry = self._execute_single_rollback(
                entry,
                actor_id=actor_id,
                reason=reason or f"Cascade rollback from {trace_id}",
                parent_trace_id=trace_id if entry.id != root.id else None,
            )
            rolled_back.append(entry.id)
            rollback_traces.append(rb_entry.id)

        return RollbackResult(
            success=True,
            rolled_back=tuple(rolled_back),
            rollback_trace_ids=tuple(rollback_traces),
            denied=tuple(denied),
        )

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    def _execute_single_rollback(
        self,
        entry: TraceEntry,
        *,
        actor_id: str,
        reason: str = "",
        parent_trace_id: str | None = None,
    ) -> TraceEntry:
        """Execute the rollback for a single trace entry.

        Restores the object's state to ``before_state`` and creates
        a ROLLBACK trace entry.
        """
        # Restore state if the target is an object with a before_state
        self._apply_rollback_state(entry)

        # Record rollback trace
        return self._writer.record(
            actor_id=actor_id,
            action=ActionType.ROLLBACK,
            target_type=entry.target_type,
            target_id=entry.target_id,
            scope_id=entry.scope_id,
            before_state=entry.after_state,
            after_state=entry.before_state,
            metadata={
                "rolled_back_trace_id": entry.id,
                "rolled_back_action": entry.action.value,
                "reason": reason,
            },
            parent_trace_id=parent_trace_id,
        )

    def _apply_rollback_state(self, entry: TraceEntry) -> None:
        """Apply the rollback by restoring database state.

        For object updates/creates, this restores the ``current_version``
        pointer.  The target is matched by looking up the ``target_id``
        in the ``scoped_objects`` table (since ``target_type`` stores the
        object's custom type, e.g. ``"invoice"``, not the literal
        ``"object"``).  For other target types, the before/after state in
        the rollback trace itself serves as the record.
        """
        # Check if this target is a scoped object (target_type stores
        # the object's custom type, not the literal "object").
        check_stmt = sa.select(scoped_objects.c.id).where(
            scoped_objects.c.id == entry.target_id,
        )
        check_sql, check_params = compile_for(check_stmt, self._backend.dialect)
        obj_row = self._backend.fetch_one(check_sql, check_params)

        if obj_row is not None and entry.before_state is not None:
            # Restore object's current_version to the version before the change
            before_version = entry.before_state.get("current_version")
            if before_version is not None:
                stmt = (
                    sa.update(scoped_objects)
                    .where(scoped_objects.c.id == entry.target_id)
                    .values(current_version=before_version)
                )
                sql, params = compile_for(stmt, self._backend.dialect)
                self._backend.execute(sql, params)
            return
        elif obj_row is not None and entry.before_state is None:
            # Rolling back a create — tombstone the object
            stmt = (
                sa.update(scoped_objects)
                .where(scoped_objects.c.id == entry.target_id)
                .values(lifecycle="ARCHIVED")
            )
            sql, params = compile_for(stmt, self._backend.dialect)
            self._backend.execute(sql, params)
            return

        # Check if this target is an environment
        env_stmt = sa.select(environments.c.id).where(
            environments.c.id == entry.target_id,
        )
        env_sql, env_params = compile_for(env_stmt, self._backend.dialect)
        env_row = self._backend.fetch_one(env_sql, env_params)

        if env_row is not None and entry.before_state is not None:
            # Restore environment state from before_state
            values: dict[str, Any] = {}
            if "state" in entry.before_state:
                values["state"] = entry.before_state["state"]
            if "metadata" in entry.before_state:
                import json
                values["metadata_json"] = json.dumps(entry.before_state["metadata"])
            if values:
                stmt = (
                    sa.update(environments)
                    .where(environments.c.id == entry.target_id)
                    .values(**values)
                )
                sql, params = compile_for(stmt, self._backend.dialect)
                self._backend.execute(sql, params)
        elif env_row is not None and entry.before_state is None:
            # Rolling back an environment create — mark discarded
            stmt = (
                sa.update(environments)
                .where(environments.c.id == entry.target_id)
                .values(state="discarded")
            )
            sql, params = compile_for(stmt, self._backend.dialect)
            self._backend.execute(sql, params)

    def _collect_descendants(
        self,
        parent_id: str,
        *,
        max_depth: int = 100,
    ) -> list[TraceEntry]:
        """BFS to collect descendant trace entries, bounded by max_depth."""
        descendants: list[TraceEntry] = []
        queue: list[tuple[str, int]] = [(parent_id, 0)]

        while queue:
            current, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            children = self._query.children(current)
            for child in children:
                descendants.append(child)
                queue.append((child.id, depth + 1))

        return descendants

    def _check_constraints(
        self,
        entry: TraceEntry,
        *,
        actor_id: str,
        principal_kind: str | None = None,
    ) -> None:
        """Check constraints and raise if denied."""
        if self._constraints is not None:
            self._constraints.check_or_raise(
                entry, actor_id=actor_id, principal_kind=principal_kind,
            )
