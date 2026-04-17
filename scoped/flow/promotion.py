"""Promotion — moving objects from environments into persistent scopes.

Promotion is selective: specific objects are promoted, not entire
environments.  Each promotion creates a scope projection (Layer 4)
and optionally places the object at an initial stage in a pipeline.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from scoped.exceptions import (
    EnvironmentNotFoundError,
    PromotionDeniedError,
    ScopeNotFoundError,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import (
    environments as environments_table,
    promotions,
    scoped_objects,
    scopes as scopes_table,
    stages as stages_table,
)
from scoped.storage.interface import StorageBackend
from scoped.tenancy.models import AccessLevel
from scoped.tenancy.projection import ProjectionManager
from scoped.types import ActionType, generate_id, now_utc

from scoped.flow.engine import FlowEngine
from scoped.flow.models import (
    FlowPointType,
    Promotion,
    promotion_from_row,
)
from scoped._stability import experimental


@experimental()
class PromotionManager:
    """Promote objects from environments into persistent scopes."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        flow_engine: FlowEngine | None = None,
        projection_manager: ProjectionManager | None = None,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._flow = flow_engine
        self._projections = projection_manager
        self._audit = audit_writer

    def promote(
        self,
        *,
        object_id: str,
        source_env_id: str,
        target_scope_id: str,
        promoted_by: str,
        target_stage_id: str | None = None,
        object_type: str | None = None,
        access_level: AccessLevel = AccessLevel.READ,
    ) -> Promotion:
        """Promote an object from an environment into a scope.

        Creates a scope projection so the object becomes visible in the
        target scope (Layer 4).  If a :class:`FlowEngine` is configured,
        first checks for an active channel from the environment to the
        target scope and that the object type is permitted.

        Optionally places the object at an initial stage via
        *target_stage_id*.  The stage must belong to a pipeline and
        must exist.
        """
        self._require_environment_exists(source_env_id)
        self._require_scope_exists(target_scope_id)
        self._require_object_exists(object_id)
        if target_stage_id is not None:
            self._require_stage_exists(target_stage_id)

        if self._flow is not None:
            resolution = self._flow.can_flow(
                source_type=FlowPointType.ENVIRONMENT,
                source_id=source_env_id,
                target_type=FlowPointType.SCOPE,
                target_id=target_scope_id,
                object_type=object_type,
            )
            if not resolution.allowed:
                raise PromotionDeniedError(
                    f"No flow channel permits this promotion: {resolution.reason}",
                    context={
                        "object_id": object_id,
                        "source_env_id": source_env_id,
                        "target_scope_id": target_scope_id,
                    },
                )

        if self._projections is not None:
            self._projections.project(
                scope_id=target_scope_id,
                object_id=object_id,
                projected_by=promoted_by,
                access_level=access_level,
            )

        ts = now_utc()
        pid = generate_id()

        promo = Promotion(
            id=pid,
            object_id=object_id,
            source_env_id=source_env_id,
            target_scope_id=target_scope_id,
            target_stage_id=target_stage_id,
            promoted_at=ts,
            promoted_by=promoted_by,
        )

        stmt = sa.insert(promotions).values(
            id=pid, object_id=object_id,
            source_env_id=source_env_id,
            target_scope_id=target_scope_id,
            target_stage_id=target_stage_id,
            promoted_at=ts.isoformat(),
            promoted_by=promoted_by,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit is not None:
            self._audit.record(
                actor_id=promoted_by,
                action=ActionType.PROMOTION,
                target_type="promotion",
                target_id=pid,
                scope_id=target_scope_id,
                after_state=promo.snapshot(),
            )

        return promo

    def _require_environment_exists(self, env_id: str) -> None:
        stmt = sa.select(environments_table.c.id).where(
            environments_table.c.id == env_id,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        if self._backend.fetch_one(sql, params) is None:
            raise EnvironmentNotFoundError(
                f"Environment {env_id} not found",
                context={"environment_id": env_id},
            )

    def _require_scope_exists(self, scope_id: str) -> None:
        stmt = sa.select(scopes_table.c.id).where(
            scopes_table.c.id == scope_id,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        if self._backend.fetch_one(sql, params) is None:
            raise ScopeNotFoundError(
                f"Scope {scope_id} not found",
                context={"scope_id": scope_id},
            )

    def _require_object_exists(self, object_id: str) -> None:
        stmt = sa.select(scoped_objects.c.id).where(
            scoped_objects.c.id == object_id,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        if self._backend.fetch_one(sql, params) is None:
            raise PromotionDeniedError(
                f"Object {object_id} not found",
                context={"object_id": object_id},
            )

    def _require_stage_exists(self, stage_id: str) -> None:
        stmt = sa.select(stages_table.c.id).where(stages_table.c.id == stage_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        if self._backend.fetch_one(sql, params) is None:
            raise PromotionDeniedError(
                f"Stage {stage_id} not found",
                context={"stage_id": stage_id},
            )

    def get(self, promotion_id: str) -> Promotion | None:
        stmt = sa.select(promotions).where(promotions.c.id == promotion_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return promotion_from_row(row) if row else None

    def list_promotions(
        self,
        *,
        source_env_id: str | None = None,
        target_scope_id: str | None = None,
        object_id: str | None = None,
        limit: int = 100,
    ) -> list[Promotion]:
        stmt = sa.select(promotions)
        if source_env_id is not None:
            stmt = stmt.where(promotions.c.source_env_id == source_env_id)
        if target_scope_id is not None:
            stmt = stmt.where(promotions.c.target_scope_id == target_scope_id)
        if object_id is not None:
            stmt = stmt.where(promotions.c.object_id == object_id)
        stmt = stmt.order_by(promotions.c.promoted_at.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [promotion_from_row(r) for r in rows]

    def count_promotions(self, source_env_id: str) -> int:
        stmt = (
            sa.select(sa.func.count().label("cnt"))
            .select_from(promotions)
            .where(promotions.c.source_env_id == source_env_id)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return row["cnt"] if row else 0
