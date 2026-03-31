"""Integration connection management — CRUD for external system integrations."""

from __future__ import annotations

import json
from typing import Any

from scoped.exceptions import IntegrationError
from scoped.integrations.models import Integration, integration_from_row
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc


class IntegrationManager:
    """Manage connections to external systems."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer

    def create_integration(
        self,
        *,
        name: str,
        integration_type: str,
        owner_id: str,
        description: str = "",
        scope_id: str | None = None,
        config: dict[str, Any] | None = None,
        credentials_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Integration:
        """Create a new integration connection."""
        ts = now_utc()
        iid = generate_id()
        cfg = config or {}
        meta = metadata or {}

        integration = Integration(
            id=iid,
            name=name,
            description=description,
            integration_type=integration_type,
            owner_id=owner_id,
            scope_id=scope_id,
            config=cfg,
            credentials_ref=credentials_ref,
            lifecycle=Lifecycle.ACTIVE,
            metadata=meta,
            created_at=ts,
        )

        self._backend.execute(
            """INSERT INTO integrations
               (id, name, description, integration_type, owner_id, scope_id,
                config_json, credentials_ref, created_at, lifecycle, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (iid, name, description, integration_type, owner_id, scope_id,
             json.dumps(cfg), credentials_ref, ts.isoformat(), "ACTIVE",
             json.dumps(meta)),
        )

        if self._audit is not None:
            self._audit.record(
                actor_id=owner_id,
                action=ActionType.INTEGRATION_CONNECT,
                target_type="integration",
                target_id=iid,
                after_state=integration.snapshot(),
            )

        return integration

    def get_integration(self, integration_id: str) -> Integration | None:
        row = self._backend.fetch_one(
            "SELECT * FROM integrations WHERE id = ?", (integration_id,),
        )
        return integration_from_row(row) if row else None

    def get_integration_or_raise(self, integration_id: str) -> Integration:
        i = self.get_integration(integration_id)
        if i is None:
            raise IntegrationError(
                f"Integration {integration_id} not found",
                context={"integration_id": integration_id},
            )
        return i

    def list_integrations(
        self,
        *,
        owner_id: str | None = None,
        integration_type: str | None = None,
        scope_id: str | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[Integration]:
        clauses: list[str] = []
        params: list[Any] = []
        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        if integration_type is not None:
            clauses.append("integration_type = ?")
            params.append(integration_type)
        if scope_id is not None:
            clauses.append("scope_id = ?")
            params.append(scope_id)
        if active_only:
            clauses.append("lifecycle = 'ACTIVE'")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = self._backend.fetch_all(
            f"SELECT * FROM integrations{where} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        return [integration_from_row(r) for r in rows]

    def update_config(
        self,
        integration_id: str,
        *,
        config: dict[str, Any],
        actor_id: str,
    ) -> Integration:
        """Update an integration's non-secret configuration."""
        integration = self.get_integration_or_raise(integration_id)
        self._backend.execute(
            "UPDATE integrations SET config_json = ? WHERE id = ?",
            (json.dumps(config), integration_id),
        )
        integration.config = config

        if self._audit is not None:
            self._audit.record(
                actor_id=actor_id,
                action=ActionType.UPDATE,
                target_type="integration",
                target_id=integration_id,
                after_state=integration.snapshot(),
            )

        return integration

    def archive_integration(
        self,
        integration_id: str,
        *,
        actor_id: str,
    ) -> None:
        """Archive (disconnect) an integration."""
        self._backend.execute(
            "UPDATE integrations SET lifecycle = 'ARCHIVED' WHERE id = ?",
            (integration_id,),
        )

        if self._audit is not None:
            self._audit.record(
                actor_id=actor_id,
                action=ActionType.INTEGRATION_DISCONNECT,
                target_type="integration",
                target_id=integration_id,
            )
