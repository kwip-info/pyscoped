"""Pipeline and stage management.

Defines and executes stage pipelines — ordered sequences of stages
that objects progress through.  Stage transitions are traced and
can be rule-governed.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from scoped.exceptions import FlowError, StageTransitionDeniedError
from scoped.storage._query import compile_for
from scoped.storage._schema import pipelines, stage_transitions, stages
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
from scoped._stability import experimental


@experimental()
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

        stmt = sa.insert(pipelines).values(
            id=pid, name=name, description=description,
            owner_id=owner_id, created_at=ts.isoformat(),
            lifecycle="ACTIVE",
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        self._trace(
            actor_id=owner_id,
            action=ActionType.CREATE,
            target_type="pipeline",
            target_id=pid,
            after_state={"name": name, "description": description},
        )

        return pipeline

    def get_pipeline(self, pipeline_id: str) -> Pipeline | None:
        stmt = sa.select(pipelines).where(pipelines.c.id == pipeline_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return pipeline_from_row(row) if row else None

    def list_pipelines(
        self,
        *,
        owner_id: str | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[Pipeline]:
        stmt = sa.select(pipelines)
        if owner_id:
            stmt = stmt.where(pipelines.c.owner_id == owner_id)
        if active_only:
            stmt = stmt.where(pipelines.c.lifecycle == "ACTIVE")
        stmt = stmt.order_by(pipelines.c.created_at.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [pipeline_from_row(r) for r in rows]

    def archive_pipeline(self, pipeline_id: str, *, archived_by: str) -> None:
        stmt = (
            sa.update(pipelines)
            .where(pipelines.c.id == pipeline_id)
            .values(lifecycle="ARCHIVED")
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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

        stmt = sa.insert(stages).values(
            id=sid, pipeline_id=pipeline_id,
            name=name, ordinal=ordinal,
            metadata_json=json.dumps(meta),
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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
        stmt = sa.select(stages).where(stages.c.id == stage_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return stage_from_row(row) if row else None

    def get_stages(self, pipeline_id: str) -> list[Stage]:
        """Get all stages for a pipeline, ordered by ordinal."""
        stmt = (
            sa.select(stages)
            .where(stages.c.pipeline_id == pipeline_id)
            .order_by(stages.c.ordinal.asc())
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [stage_from_row(r) for r in rows]

    def get_stage_by_name(self, pipeline_id: str, name: str) -> Stage | None:
        stmt = (
            sa.select(stages)
            .where(stages.c.pipeline_id == pipeline_id, stages.c.name == name)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
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

        stmt = sa.insert(stage_transitions).values(
            id=tid, object_id=object_id,
            from_stage_id=from_stage_id, to_stage_id=to_stage_id,
            transitioned_at=ts.isoformat(),
            transitioned_by=transitioned_by, reason=reason,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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
        stmt = (
            sa.select(stage_transitions)
            .where(stage_transitions.c.object_id == object_id)
            .order_by(stage_transitions.c.transitioned_at.desc())
            .limit(1)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return transition_from_row(row) if row else None

    def get_transition_history(
        self,
        object_id: str,
        *,
        limit: int = 100,
    ) -> list[StageTransition]:
        """Get transition history for an object, newest first."""
        stmt = (
            sa.select(stage_transitions)
            .where(stage_transitions.c.object_id == object_id)
            .order_by(stage_transitions.c.transitioned_at.desc())
            .limit(limit)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
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
