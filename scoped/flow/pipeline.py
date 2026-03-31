"""Pipeline and stage management.

Defines and executes stage pipelines — ordered sequences of stages
that objects progress through.  Stage transitions are traced and
can be rule-governed.
"""

from __future__ import annotations

import json
from typing import Any

from scoped.exceptions import FlowError, StageTransitionDeniedError
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc

from scoped.flow.models import (
    Pipeline,
    Stage,
    StageTransition,
    pipeline_from_row,
    stage_from_row,
    transition_from_row,
)


class PipelineManager:
    """Manages pipelines, stages, and stage transitions."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer

    # ------------------------------------------------------------------
    # Pipelines
    # ------------------------------------------------------------------

    def create_pipeline(
        self,
        *,
        name: str,
        owner_id: str,
        description: str = "",
    ) -> Pipeline:
        """Create a new pipeline."""
        ts = now_utc()
        pid = generate_id()

        pipeline = Pipeline(
            id=pid, name=name, owner_id=owner_id,
            description=description, created_at=ts,
        )

        self._backend.execute(
            """INSERT INTO pipelines
               (id, name, description, owner_id, created_at, lifecycle)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (pid, name, description, owner_id, ts.isoformat(), "ACTIVE"),
        )

        self._trace(
            actor_id=owner_id,
            action=ActionType.CREATE,
            target_type="pipeline",
            target_id=pid,
            after_state={"name": name, "description": description},
        )

        return pipeline

    def get_pipeline(self, pipeline_id: str) -> Pipeline | None:
        row = self._backend.fetch_one(
            "SELECT * FROM pipelines WHERE id = ?", (pipeline_id,),
        )
        return pipeline_from_row(row) if row else None

    def list_pipelines(
        self,
        *,
        owner_id: str | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[Pipeline]:
        clauses: list[str] = []
        params: list[Any] = []
        if owner_id:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        if active_only:
            clauses.append("lifecycle = 'ACTIVE'")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = self._backend.fetch_all(
            f"SELECT * FROM pipelines{where} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        return [pipeline_from_row(r) for r in rows]

    def archive_pipeline(self, pipeline_id: str, *, archived_by: str) -> None:
        self._backend.execute(
            "UPDATE pipelines SET lifecycle = 'ARCHIVED' WHERE id = ?",
            (pipeline_id,),
        )

        self._trace(
            actor_id=archived_by,
            action=ActionType.LIFECYCLE_CHANGE,
            target_type="pipeline",
            target_id=pipeline_id,
            before_state={"lifecycle": "ACTIVE"},
            after_state={"lifecycle": "ARCHIVED"},
        )

    # ------------------------------------------------------------------
    # Stages
    # ------------------------------------------------------------------

    def add_stage(
        self,
        pipeline_id: str,
        *,
        name: str,
        ordinal: int,
        metadata: dict[str, Any] | None = None,
    ) -> Stage:
        """Add a stage to a pipeline."""
        sid = generate_id()
        meta = metadata or {}

        stage = Stage(
            id=sid, pipeline_id=pipeline_id,
            name=name, ordinal=ordinal, metadata=meta,
        )

        self._backend.execute(
            """INSERT INTO stages
               (id, pipeline_id, name, ordinal, metadata_json)
               VALUES (?, ?, ?, ?, ?)""",
            (sid, pipeline_id, name, ordinal, json.dumps(meta)),
        )

        # Use pipeline owner as actor for stage creation
        pipeline = self.get_pipeline(pipeline_id)
        if pipeline is not None:
            self._trace(
                actor_id=pipeline.owner_id,
                action=ActionType.CREATE,
                target_type="stage",
                target_id=sid,
                after_state={"name": name, "ordinal": ordinal, "pipeline_id": pipeline_id},
            )

        return stage

    def get_stage(self, stage_id: str) -> Stage | None:
        row = self._backend.fetch_one(
            "SELECT * FROM stages WHERE id = ?", (stage_id,),
        )
        return stage_from_row(row) if row else None

    def get_stages(self, pipeline_id: str) -> list[Stage]:
        """Get all stages for a pipeline, ordered by ordinal."""
        rows = self._backend.fetch_all(
            "SELECT * FROM stages WHERE pipeline_id = ? ORDER BY ordinal ASC",
            (pipeline_id,),
        )
        return [stage_from_row(r) for r in rows]

    def get_stage_by_name(self, pipeline_id: str, name: str) -> Stage | None:
        row = self._backend.fetch_one(
            "SELECT * FROM stages WHERE pipeline_id = ? AND name = ?",
            (pipeline_id, name),
        )
        return stage_from_row(row) if row else None

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def transition(
        self,
        object_id: str,
        to_stage_id: str,
        *,
        transitioned_by: str,
        from_stage_id: str | None = None,
        reason: str = "",
    ) -> StageTransition:
        """Move an object to a new stage.

        If *from_stage_id* is ``None``, this is an initial placement.
        """
        ts = now_utc()
        tid = generate_id()

        # Validate target stage exists
        to_stage = self.get_stage(to_stage_id)
        if to_stage is None:
            raise FlowError(
                f"Target stage '{to_stage_id}' not found",
                context={"to_stage_id": to_stage_id},
            )

        # Validate from_stage exists if provided
        if from_stage_id is not None:
            from_stage = self.get_stage(from_stage_id)
            if from_stage is None:
                raise FlowError(
                    f"Source stage '{from_stage_id}' not found",
                    context={"from_stage_id": from_stage_id},
                )

        trans = StageTransition(
            id=tid, object_id=object_id,
            from_stage_id=from_stage_id, to_stage_id=to_stage_id,
            transitioned_at=ts, transitioned_by=transitioned_by,
            reason=reason,
        )

        self._backend.execute(
            """INSERT INTO stage_transitions
               (id, object_id, from_stage_id, to_stage_id,
                transitioned_at, transitioned_by, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                tid, object_id, from_stage_id, to_stage_id,
                ts.isoformat(), transitioned_by, reason,
            ),
        )

        self._trace(
            actor_id=transitioned_by,
            action=ActionType.STAGE_TRANSITION,
            target_type="stage_transition",
            target_id=tid,
            before_state={"stage_id": from_stage_id} if from_stage_id else None,
            after_state={"stage_id": to_stage_id, "object_id": object_id},
        )

        return trans

    def get_current_stage(self, object_id: str) -> StageTransition | None:
        """Get the most recent stage transition for an object."""
        row = self._backend.fetch_one(
            """SELECT * FROM stage_transitions
               WHERE object_id = ?
               ORDER BY transitioned_at DESC LIMIT 1""",
            (object_id,),
        )
        return transition_from_row(row) if row else None

    def get_transition_history(
        self,
        object_id: str,
        *,
        limit: int = 100,
    ) -> list[StageTransition]:
        """Get transition history for an object, newest first."""
        rows = self._backend.fetch_all(
            """SELECT * FROM stage_transitions
               WHERE object_id = ?
               ORDER BY transitioned_at DESC LIMIT ?""",
            (object_id, limit),
        )
        return [transition_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _trace(
        self,
        *,
        actor_id: str,
        action: ActionType,
        target_type: str,
        target_id: str,
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
    ) -> None:
        if self._audit is not None:
            self._audit.record(
                actor_id=actor_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                before_state=before_state,
                after_state=after_state,
            )
