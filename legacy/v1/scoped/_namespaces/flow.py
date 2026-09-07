"""Flow namespace — directional channels and flow resolution.

Usage::

    import scoped
    from scoped.flow.models import FlowPointType

    with scoped.as_principal(alice):
        channel = scoped.flow.create_channel(
            name="env->staging",
            source_type=FlowPointType.ENVIRONMENT, source_id=env.id,
            target_type=FlowPointType.SCOPE, target_id=staging.id,
            allowed_types=["invoice"],
        )

        resolution = scoped.flow.can_flow(
            source_type=FlowPointType.ENVIRONMENT, source_id=env.id,
            target_type=FlowPointType.SCOPE, target_id=staging.id,
            object_type="invoice",
        )
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from scoped._namespaces._base import _resolve_principal_id, _to_id

if TYPE_CHECKING:
    from scoped.flow.engine import FlowResolution
    from scoped.flow.models import FlowChannel, FlowPointType


class FlowNamespace:
    """Context-aware wrapper around :class:`FlowEngine`."""

    def __init__(self, services: Any) -> None:
        self._svc = services

    def create_channel(
        self,
        *,
        name: str,
        source_type: FlowPointType,
        source_id: str | Any,
        target_type: FlowPointType,
        target_id: str | Any,
        owner_id: str | None = None,
        allowed_types: list[str] | None = None,
    ) -> FlowChannel:
        return self._svc.flow.create_channel(
            name=name,
            source_type=source_type, source_id=_to_id(source_id),
            target_type=target_type, target_id=_to_id(target_id),
            owner_id=_resolve_principal_id(owner_id),
            allowed_types=allowed_types,
        )

    def get_channel(self, channel: str | Any) -> FlowChannel | None:
        return self._svc.flow.get_channel(_to_id(channel))

    def list_channels(
        self,
        *,
        source_type: FlowPointType | None = None,
        source_id: str | Any | None = None,
        target_type: FlowPointType | None = None,
        target_id: str | Any | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[FlowChannel]:
        return self._svc.flow.list_channels(
            source_type=source_type,
            source_id=_to_id(source_id) if source_id is not None else None,
            target_type=target_type,
            target_id=_to_id(target_id) if target_id is not None else None,
            active_only=active_only,
            limit=limit,
        )

    def archive_channel(
        self, channel: str | Any, *, archived_by: str | None = None,
    ) -> None:
        self._svc.flow.archive_channel(
            _to_id(channel),
            archived_by=_resolve_principal_id(archived_by),
        )

    def can_flow(
        self,
        *,
        source_type: FlowPointType,
        source_id: str | Any,
        target_type: FlowPointType,
        target_id: str | Any,
        object_type: str | None = None,
    ) -> FlowResolution:
        return self._svc.flow.can_flow(
            source_type=source_type, source_id=_to_id(source_id),
            target_type=target_type, target_id=_to_id(target_id),
            object_type=object_type,
        )

    def can_flow_or_raise(
        self,
        *,
        source_type: FlowPointType,
        source_id: str | Any,
        target_type: FlowPointType,
        target_id: str | Any,
        object_type: str | None = None,
    ) -> FlowResolution:
        return self._svc.flow.can_flow_or_raise(
            source_type=source_type, source_id=_to_id(source_id),
            target_type=target_type, target_id=_to_id(target_id),
            object_type=object_type,
        )

    def find_routes(
        self,
        *,
        source_type: FlowPointType,
        source_id: str | Any,
        object_type: str | None = None,
    ) -> list[FlowChannel]:
        return self._svc.flow.find_routes(
            source_type=source_type, source_id=_to_id(source_id),
            object_type=object_type,
        )
