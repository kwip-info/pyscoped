"""Pipelines namespace — stages, transitions, and pipeline lifecycle.

Usage::

    import scoped

    with scoped.as_principal(alice):
        p = scoped.pipelines.create("Code Review")
        draft = scoped.pipelines.add_stage(p, name="draft", ordinal=0)
        review = scoped.pipelines.add_stage(p, name="review", ordinal=1)

        scoped.pipelines.transition(doc, draft)
        scoped.pipelines.transition(doc, review, from_stage=draft)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from scoped._namespaces._base import _resolve_principal_id, _to_id

if TYPE_CHECKING:
    from scoped.flow.models import Pipeline, Stage, StageTransition


class PipelinesNamespace:
    """Context-aware wrapper around :class:`PipelineManager`."""

    def __init__(self, services: Any) -> None:
        self._svc = services

    # -- Pipeline CRUD ----------------------------------------------------

    def create(
        self,
        name: str,
        *,
        owner_id: str | None = None,
        description: str = "",
    ) -> Pipeline:
        """Create a pipeline owned by the acting principal."""
        return self._svc.pipelines.create_pipeline(
            name=name,
            owner_id=_resolve_principal_id(owner_id),
            description=description,
        )

    def get(self, pipeline: str | Any) -> Pipeline | None:
        return self._svc.pipelines.get_pipeline(_to_id(pipeline))

    def list(
        self,
        *,
        owner_id: str | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[Pipeline]:
        return self._svc.pipelines.list_pipelines(
            owner_id=owner_id, active_only=active_only, limit=limit,
        )

    def archive(self, pipeline: str | Any, *, archived_by: str | None = None) -> None:
        self._svc.pipelines.archive_pipeline(
            _to_id(pipeline),
            archived_by=_resolve_principal_id(archived_by),
        )

    # -- Stages -----------------------------------------------------------

    def add_stage(
        self,
        pipeline: str | Any,
        *,
        name: str,
        ordinal: int,
        metadata: dict[str, Any] | None = None,
    ) -> Stage:
        return self._svc.pipelines.add_stage(
            _to_id(pipeline), name=name, ordinal=ordinal, metadata=metadata,
        )

    def get_stage(self, stage: str | Any) -> Stage | None:
        return self._svc.pipelines.get_stage(_to_id(stage))

    def stages(self, pipeline: str | Any) -> list[Stage]:
        return self._svc.pipelines.get_stages(_to_id(pipeline))

    def stage_by_name(self, pipeline: str | Any, name: str) -> Stage | None:
        return self._svc.pipelines.get_stage_by_name(_to_id(pipeline), name)

    # -- Transitions ------------------------------------------------------

    def transition(
        self,
        obj: str | Any,
        to_stage: str | Any,
        *,
        transitioned_by: str | None = None,
        from_stage: str | Any | None = None,
        reason: str = "",
    ) -> StageTransition:
        return self._svc.pipelines.transition(
            _to_id(obj), _to_id(to_stage),
            transitioned_by=_resolve_principal_id(transitioned_by),
            from_stage_id=_to_id(from_stage) if from_stage is not None else None,
            reason=reason,
        )

    def current_stage(self, obj: str | Any) -> StageTransition | None:
        return self._svc.pipelines.get_current_stage(_to_id(obj))

    def history(self, obj: str | Any, *, limit: int = 100) -> list[StageTransition]:
        return self._svc.pipelines.get_transition_history(
            _to_id(obj), limit=limit,
        )
