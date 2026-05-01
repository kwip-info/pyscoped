"""Integrations namespace — Layer 12 external system connections.

Usage::

    import scoped

    with scoped.as_principal(alice):
        gh = scoped.integrations.create("github-acme", integration_type="github")
        scoped.integrations.update_config(gh, {"org": "acme"})
        scoped.integrations.archive(gh)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from scoped._namespaces._base import _resolve_principal_id, _to_id

if TYPE_CHECKING:
    from scoped.integrations.models import Integration


class IntegrationsNamespace:
    """Simplified API for Layer 12 integration connections.

    Wraps ``IntegrationManager`` with context-aware defaults.
    """

    def __init__(self, services: Any) -> None:
        self._svc = services

    def create(
        self,
        name: str,
        *,
        integration_type: str,
        owner_id: str | None = None,
        description: str = "",
        scope_id: str | Any | None = None,
        config: dict[str, Any] | None = None,
        credentials_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Integration:
        """Create a new integration connection."""
        return self._svc.integrations.create_integration(
            name=name,
            integration_type=integration_type,
            owner_id=_resolve_principal_id(owner_id),
            description=description,
            scope_id=_to_id(scope_id) if scope_id is not None else None,
            config=config,
            credentials_ref=credentials_ref,
            metadata=metadata,
        )

    def get(self, integration_id: str) -> Integration | None:
        return self._svc.integrations.get_integration(integration_id)

    def list(
        self,
        *,
        owner_id: str | None = None,
        integration_type: str | None = None,
        scope_id: str | Any | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[Integration]:
        return self._svc.integrations.list_integrations(
            owner_id=owner_id,
            integration_type=integration_type,
            scope_id=_to_id(scope_id) if scope_id is not None else None,
            active_only=active_only,
            limit=limit,
        )

    def update_config(
        self,
        integration: str | Any,
        config: dict[str, Any],
        *,
        actor_id: str | None = None,
    ) -> Integration:
        """Update an integration's non-secret configuration."""
        return self._svc.integrations.update_config(
            _to_id(integration),
            config=config,
            actor_id=_resolve_principal_id(actor_id),
        )

    def archive(
        self,
        integration: str | Any,
        *,
        actor_id: str | None = None,
    ) -> None:
        """Archive (disconnect) an integration."""
        self._svc.integrations.archive_integration(
            _to_id(integration),
            actor_id=_resolve_principal_id(actor_id),
        )
