"""Promotions namespace — move objects from environments into scopes.

Usage::

    import scoped

    with scoped.as_principal(alice):
        promo = scoped.promotions.promote(
            obj=doc, source_env=env, target_scope=team,
        )

        # All promotions out of a given env:
        promos = scoped.promotions.list(source_env=env)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from scoped._namespaces._base import _resolve_principal_id, _to_id

if TYPE_CHECKING:
    from scoped.flow.models import Promotion
    from scoped.tenancy.models import AccessLevel


class PromotionsNamespace:
    """Context-aware wrapper around :class:`PromotionManager`."""

    def __init__(self, services: Any) -> None:
        self._svc = services

    def promote(
        self,
        *,
        obj: str | Any,
        source_env: str | Any,
        target_scope: str | Any,
        promoted_by: str | None = None,
        target_stage: str | Any | None = None,
        object_type: str | None = None,
        access_level: AccessLevel | None = None,
    ) -> Promotion:
        kwargs: dict[str, Any] = {
            "object_id": _to_id(obj),
            "source_env_id": _to_id(source_env),
            "target_scope_id": _to_id(target_scope),
            "promoted_by": _resolve_principal_id(promoted_by),
        }
        if target_stage is not None:
            kwargs["target_stage_id"] = _to_id(target_stage)
        if object_type is not None:
            kwargs["object_type"] = object_type
        if access_level is not None:
            kwargs["access_level"] = access_level
        return self._svc.promotions.promote(**kwargs)

    def get(self, promotion: str | Any) -> Promotion | None:
        return self._svc.promotions.get(_to_id(promotion))

    def list(
        self,
        *,
        source_env: str | Any | None = None,
        target_scope: str | Any | None = None,
        obj: str | Any | None = None,
        limit: int = 100,
    ) -> list[Promotion]:
        return self._svc.promotions.list_promotions(
            source_env_id=_to_id(source_env) if source_env is not None else None,
            target_scope_id=_to_id(target_scope) if target_scope is not None else None,
            object_id=_to_id(obj) if obj is not None else None,
            limit=limit,
        )

    def count(self, source_env: str | Any) -> int:
        return self._svc.promotions.count_promotions(_to_id(source_env))
