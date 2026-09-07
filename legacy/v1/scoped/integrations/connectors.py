"""Integration connection management — CRUD for external system integrations."""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from scoped.exceptions import AccessDeniedError, IntegrationError
from scoped.integrations.models import Integration, integration_from_row
from scoped.storage._query import compile_for
from scoped.storage._schema import integrations
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc
from scoped._stability import stable


@stable(since="1.7.0")
class IntegrationManager:
    """Manage connections to external systems."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
        rule_engine: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer
        self._rule_engine = rule_engine

    def _check_rule(
        self,
        *,
        action: str,
        principal_id: str,
        object_id: str | None = None,
        scope_id: str | None = None,
    ) -> None:
        """Layer 5 rule gate. Mirrors the pattern in Layer 9."""
        if self._rule_engine is None:
            return
        result = self._rule_engine.evaluate(
            action=action,
            principal_id=principal_id,
            object_type="integration",
            object_id=object_id,
            scope_id=scope_id,
        )
        if not result.allowed and (result.deny_rules or result.matching_rules):
            deny_names = [r.name for r in result.deny_rules]
            raise AccessDeniedError(
                f"{action} denied by rule(s): {deny_names}",
                context={
                    "action": action,
                    "principal_id": principal_id,
                    "object_id": object_id,
                    "scope_id": scope_id,
                    "deny_rules": deny_names,
                },
            )

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
        self._check_rule(
            action="integration_connect",
            principal_id=owner_id,
            scope_id=scope_id,
        )
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

        stmt = sa.insert(integrations).values(
            id=iid,
            name=name,
            description=description,
            integration_type=integration_type,
            owner_id=owner_id,
            scope_id=scope_id,
            config_json=json.dumps(cfg),
            credentials_ref=credentials_ref,
            created_at=ts.isoformat(),
            lifecycle="ACTIVE",
            metadata_json=json.dumps(meta),
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

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
        stmt = sa.select(integrations).where(integrations.c.id == integration_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
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
        stmt = sa.select(integrations)
        if owner_id is not None:
            stmt = stmt.where(integrations.c.owner_id == owner_id)
        if integration_type is not None:
            stmt = stmt.where(integrations.c.integration_type == integration_type)
        if scope_id is not None:
            stmt = stmt.where(integrations.c.scope_id == scope_id)
        if active_only:
            stmt = stmt.where(integrations.c.lifecycle == "ACTIVE")
        stmt = stmt.order_by(integrations.c.created_at.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [integration_from_row(r) for r in rows]

    def _require_owner(
        self,
        integration: Integration,
        actor_id: str,
    ) -> None:
        """Raise AccessDeniedError if actor is not the integration owner."""
        if integration.owner_id != actor_id:
            raise AccessDeniedError(
                f"Principal '{actor_id}' is not the owner of integration "
                f"'{integration.id}'",
                context={
                    "integration_id": integration.id,
                    "actor_id": actor_id,
                    "owner_id": integration.owner_id,
                },
            )

    def update_config(
        self,
        integration_id: str,
        *,
        config: dict[str, Any],
        actor_id: str,
    ) -> Integration:
        """Update an integration's non-secret configuration.

        Only the integration owner may update its config.
        """
        integration = self.get_integration_or_raise(integration_id)
        self._require_owner(integration, actor_id)
        stmt = sa.update(integrations).where(
            integrations.c.id == integration_id,
        ).values(config_json=json.dumps(config))
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
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
        """Archive (disconnect) an integration.

        Raises ``IntegrationError`` if the integration does not exist.
        Only the integration owner may archive it.
        """
        integration = self.get_integration_or_raise(integration_id)
        self._require_owner(integration, actor_id)
        stmt = sa.update(integrations).where(
            integrations.c.id == integration_id,
        ).values(lifecycle="ARCHIVED")
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit is not None:
            self._audit.record(
                actor_id=actor_id,
                action=ActionType.INTEGRATION_DISCONNECT,
                target_type="integration",
                target_id=integration_id,
            )
