"""Promotion — moving objects from environments into persistent scopes.

Promotion is selective: specific objects are promoted, not entire
environments.  Each promotion creates a scope projection (Layer 4)
and optionally places the object at an initial stage in a pipeline.
"""

from __future__ import annotations

from typing import Any

from scoped.exceptions import FlowBlockedError, PromotionDeniedError
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, generate_id, now_utc

from scoped.flow.engine import FlowEngine
from scoped.flow.models import (
    FlowPointType,
    Promotion,
    promotion_from_row,
)


class PromotionManager:
    """Promote objects from environments into persistent scopes."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        flow_engine: FlowEngine | None = None,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._flow = flow_engine
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
    ) -> Promotion:
        """Promote an object from an environment into a scope.

        If a :class:`FlowEngine` is configured, checks for an active
        channel from the environment to the target scope.

        Optionally places the object at an initial stage via
        *target_stage_id*.
        """
        # Check flow channel if engine available
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

        self._backend.execute(
            """INSERT INTO promotions
               (id, object_id, source_env_id, target_scope_id,
                target_stage_id, promoted_at, promoted_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                pid, object_id, source_env_id, target_scope_id,
                target_stage_id, ts.isoformat(), promoted_by,
            ),
        )

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

    def get(self, promotion_id: str) -> Promotion | None:
        row = self._backend.fetch_one(
            "SELECT * FROM promotions WHERE id = ?", (promotion_id,),
        )
        return promotion_from_row(row) if row else None

    def list_promotions(
        self,
        *,
        source_env_id: str | None = None,
        target_scope_id: str | None = None,
        object_id: str | None = None,
        limit: int = 100,
    ) -> list[Promotion]:
        clauses: list[str] = []
        params: list[Any] = []

        if source_env_id is not None:
            clauses.append("source_env_id = ?")
            params.append(source_env_id)
        if target_scope_id is not None:
            clauses.append("target_scope_id = ?")
            params.append(target_scope_id)
        if object_id is not None:
            clauses.append("object_id = ?")
            params.append(object_id)

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)

        rows = self._backend.fetch_all(
            f"SELECT * FROM promotions{where} ORDER BY promoted_at DESC LIMIT ?",
            tuple(params),
        )
        return [promotion_from_row(r) for r in rows]

    def count_promotions(self, source_env_id: str) -> int:
        row = self._backend.fetch_one(
            "SELECT COUNT(*) as cnt FROM promotions WHERE source_env_id = ?",
            (source_env_id,),
        )
        return row["cnt"] if row else 0
