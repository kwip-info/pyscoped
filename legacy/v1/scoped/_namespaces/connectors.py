"""Connectors namespace — Layer 13 cross-organization federation.

Usage::

    import scoped

    with scoped.as_principal(alice):
        c = scoped.connectors.propose(
            name="acme-beta",
            local_org_id=org1, remote_org_id=org2,
            remote_endpoint="https://beta.example.com/sync",
        )
        scoped.connectors.submit(c)
        scoped.connectors.approve(c)

        scoped.connectors.add_policy(
            c, policy_type=PolicyType.DENY_TYPES,
            config={"types": ["InternalMemo"]},
        )

        traffic = scoped.connectors.sync(c, object_type="Document",
                                          object_id="doc-1")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from scoped._namespaces._base import _resolve_principal_id, _to_id

if TYPE_CHECKING:
    from scoped.connector.models import (
        Connector,
        ConnectorDirection,
        ConnectorPolicy,
        ConnectorState,
        ConnectorTraffic,
        PolicyType,
    )


class ConnectorsNamespace:
    """Simplified API for Layer 13 connector federation.

    Wraps ``ConnectorManager`` with context-aware defaults. The acting
    principal is inferred from ``ScopedContext`` when not passed
    explicitly.
    """

    def __init__(self, services: Any) -> None:
        self._svc = services

    # -- Lifecycle ---------------------------------------------------------

    def propose(
        self,
        *,
        name: str,
        local_org_id: str | Any,
        remote_org_id: str | Any,
        remote_endpoint: str,
        created_by: str | None = None,
        description: str = "",
        direction: ConnectorDirection | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Connector:
        """Propose a new connector (state=proposed)."""
        from scoped.connector.models import ConnectorDirection as _CD

        kwargs: dict[str, Any] = {
            "name": name,
            "local_org_id": _to_id(local_org_id),
            "remote_org_id": _to_id(remote_org_id),
            "remote_endpoint": remote_endpoint,
            "created_by": _resolve_principal_id(created_by),
            "description": description,
            "metadata": metadata,
        }
        if direction is not None:
            kwargs["direction"] = direction
        else:
            kwargs["direction"] = _CD.BIDIRECTIONAL
        return self._svc.connectors.propose(**kwargs)

    def submit(
        self,
        connector: str | Any,
        *,
        actor_id: str | None = None,
    ) -> Connector:
        """proposed -> pending_approval."""
        return self._svc.connectors.submit_for_approval(
            _to_id(connector), actor_id=_resolve_principal_id(actor_id),
        )

    def approve(
        self,
        connector: str | Any,
        *,
        actor_id: str | None = None,
    ) -> Connector:
        """pending_approval -> active."""
        return self._svc.connectors.approve(
            _to_id(connector), actor_id=_resolve_principal_id(actor_id),
        )

    def reject(
        self,
        connector: str | Any,
        *,
        actor_id: str | None = None,
    ) -> Connector:
        """proposed|pending_approval -> rejected."""
        return self._svc.connectors.reject(
            _to_id(connector), actor_id=_resolve_principal_id(actor_id),
        )

    def suspend(
        self,
        connector: str | Any,
        *,
        actor_id: str | None = None,
    ) -> Connector:
        """active -> suspended."""
        return self._svc.connectors.suspend(
            _to_id(connector), actor_id=_resolve_principal_id(actor_id),
        )

    def reactivate(
        self,
        connector: str | Any,
        *,
        actor_id: str | None = None,
    ) -> Connector:
        """suspended -> active."""
        return self._svc.connectors.reactivate(
            _to_id(connector), actor_id=_resolve_principal_id(actor_id),
        )

    def revoke(
        self,
        connector: str | Any,
        *,
        actor_id: str | None = None,
    ) -> Connector:
        """-> revoked (terminal)."""
        return self._svc.connectors.revoke(
            _to_id(connector), actor_id=_resolve_principal_id(actor_id),
        )

    # -- Queries -----------------------------------------------------------

    def get(self, connector_id: str) -> Connector | None:
        return self._svc.connectors.get_connector(connector_id)

    def list(
        self,
        *,
        local_org_id: str | Any | None = None,
        state: ConnectorState | None = None,
        limit: int = 100,
    ) -> list[Connector]:
        return self._svc.connectors.list_connectors(
            local_org_id=_to_id(local_org_id) if local_org_id is not None else None,
            state=state,
            limit=limit,
        )

    # -- Policies ----------------------------------------------------------

    def add_policy(
        self,
        connector: str | Any,
        *,
        policy_type: PolicyType,
        config: dict[str, Any],
        created_by: str | None = None,
    ) -> ConnectorPolicy:
        """Attach a traffic policy to a connector (creator-only)."""
        return self._svc.connectors.add_policy(
            connector_id=_to_id(connector),
            policy_type=policy_type,
            config=config,
            created_by=_resolve_principal_id(created_by),
        )

    def policies(self, connector: str | Any) -> list[ConnectorPolicy]:
        return self._svc.connectors.get_policies(_to_id(connector))

    def check_policy(
        self,
        connector: str | Any,
        object_type: str,
    ) -> bool:
        """Returns True if the object type is allowed through the connector."""
        return self._svc.connectors.check_policy(_to_id(connector), object_type)

    # -- Traffic / sync ----------------------------------------------------

    def sync(
        self,
        connector: str | Any,
        *,
        object_type: str,
        object_id: str | Any | None = None,
        direction: str = "outbound",
        actor_id: str | None = None,
        size_bytes: int | None = None,
    ) -> ConnectorTraffic:
        """Sync an object through a connector with policy + rule checks."""
        return self._svc.connectors.sync_object(
            _to_id(connector),
            object_type=object_type,
            object_id=_to_id(object_id) if object_id is not None else None,
            direction=direction,
            actor_id=_resolve_principal_id(actor_id),
            size_bytes=size_bytes,
        )

    def traffic(
        self,
        connector: str | Any,
        *,
        direction: str | None = None,
        limit: int = 100,
    ) -> list[ConnectorTraffic]:
        """List recorded traffic events for a connector."""
        return self._svc.connectors.get_traffic(
            _to_id(connector), direction=direction, limit=limit,
        )

    # -- Federation protocol ----------------------------------------------

    def federation(self, shared_key: str) -> Any:
        """Build a backend-wired ``FederationProtocol`` for a shared key.

        Each connector pair uses its own pre-shared HMAC key. The
        returned protocol persists sender sequences and tracks
        receiver-side replay across process restarts.
        """
        return self._svc.federation_protocol(shared_key)
