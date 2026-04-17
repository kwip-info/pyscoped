"""Pipeline and stage management.

Defines and executes stage pipelines — ordered sequences of stages
that objects progress through.  Stage transitions are traced and
can be rule-governed.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from scoped.exceptions import (
    AccessDeniedError,
    FlowError,
    StageTransitionDeniedError,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import (
    pipelines,
    scoped_objects,
    stage_transitions,
    stages,
)
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, generate_id, now_utc

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

        If *from_stage_id* is ``None``, this is an initial placement and
        the object must not already have a current stage.  Otherwise,
        *from_stage_id* must equal the object's current stage.

        The caller (``transitioned_by``) must own the object.

        Both stages must belong to the same pipeline and that pipeline
        must still be active — archived pipelines reject transitions.
        """
        to_stage = self.get_stage(to_stage_id)
        if to_stage is None:
            raise FlowError(
                f"Target stage '{to_stage_id}' not found",
                context={"to_stage_id": to_stage_id},
            )

        from_stage = None
        if from_stage_id is not None:
            from_stage = self.get_stage(from_stage_id)
            if from_stage is None:
                raise FlowError(
                    f"Source stage '{from_stage_id}' not found",
                    context={"from_stage_id": from_stage_id},
                )
            if from_stage.pipeline_id != to_stage.pipeline_id:
                raise StageTransitionDeniedError(
                    "Stages belong to different pipelines — transitions must "
                    "stay within a single pipeline.",
                    context={
                        "from_stage_id": from_stage_id,
                        "to_stage_id": to_stage_id,
                        "from_pipeline_id": from_stage.pipeline_id,
                        "to_pipeline_id": to_stage.pipeline_id,
                    },
                )

        pipeline = self.get_pipeline(to_stage.pipeline_id)
        if pipeline is not None and not pipeline.is_active:
            raise StageTransitionDeniedError(
                f"Pipeline '{pipeline.name}' is archived; transitions denied.",
                context={
                    "pipeline_id": pipeline.id,
                    "lifecycle": pipeline.lifecycle.name,
                },
            )

        owner_id = self._get_object_owner(object_id)
        if owner_id is None:
            raise FlowError(
                f"Object '{object_id}' not found",
                context={"object_id": object_id},
            )
        if owner_id != transitioned_by:
            raise AccessDeniedError(
                "Only the object owner can transition its stage.",
                context={
                    "object_id": object_id,
                    "owner_id": owner_id,
                    "actor_id": transitioned_by,
                },
            )

        current = self.get_current_stage(object_id)
        current_stage_id = current.to_stage_id if current is not None else None
        if from_stage_id is None:
            if current_stage_id is not None:
                raise StageTransitionDeniedError(
                    "Object already has a current stage; pass from_stage_id "
                    "to move it or use an explicit transition.",
                    context={
                        "object_id": object_id,
                        "current_stage_id": current_stage_id,
                    },
                )
        elif current_stage_id != from_stage_id:
            raise StageTransitionDeniedError(
                "from_stage_id does not match the object's current stage.",
                context={
                    "object_id": object_id,
                    "from_stage_id": from_stage_id,
                    "current_stage_id": current_stage_id,
                },
            )

        ts = now_utc()
        tid = generate_id()

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

    def _get_object_owner(self, object_id: str) -> str | None:
        stmt = sa.select(scoped_objects.c.owner_id).where(
            scoped_objects.c.id == object_id,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return row["owner_id"] if row else None

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
