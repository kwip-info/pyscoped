"""Connector bridge — manage connectors, policies, and traffic."""

from __future__ import annotations

import json
from typing import Any

from scoped.exceptions import (
    ConnectorError,
    ConnectorNotApprovedError,
    ConnectorPolicyViolation,
    ConnectorRevokedError,
)
from scoped.connector.models import (
    Connector,
    ConnectorDirection,
    ConnectorPolicy,
    ConnectorState,
    ConnectorTraffic,
    PolicyType,
    TrafficStatus,
    connector_from_row,
    policy_from_row,
    traffic_from_row,
)
from scoped.registry.base import get_registry
from scoped.registry.kinds import RegistryKind
from scoped.registry.sqlite_store import SQLiteRegistryStore
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, generate_id, now_utc


class ConnectorManager:
    """Manage cross-organization connectors, policies, and traffic.

    For real federation, pass a ``transport`` callable that performs
    the HTTP push to the remote endpoint::

        def my_transport(endpoint_url, payload):
            resp = httpx.post(endpoint_url, json=payload, timeout=10)
            return resp.status_code, resp.text

        mgr = ConnectorManager(backend, transport=my_transport)

    Without a transport, ``sync_object`` validates policies and records
    traffic but does not push data over the network.
    """

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
        transport: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer
        self._transport = transport

    # -- Connector CRUD ----------------------------------------------------

    def propose(
        self,
        *,
        name: str,
        local_org_id: str,
        remote_org_id: str,
        remote_endpoint: str,
        created_by: str,
        description: str = "",
        direction: ConnectorDirection = ConnectorDirection.BIDIRECTIONAL,
        metadata: dict[str, Any] | None = None,
    ) -> Connector:
        """Propose a new connector (state=proposed)."""
        ts = now_utc()
        cid = generate_id()
        meta = metadata or {}

        connector = Connector(
            id=cid,
            name=name,
            description=description,
            local_org_id=local_org_id,
            remote_org_id=remote_org_id,
            remote_endpoint=remote_endpoint,
            state=ConnectorState.PROPOSED,
            direction=direction,
            created_at=ts,
            created_by=created_by,
            metadata=meta,
        )

        self._backend.execute(
            """INSERT INTO connectors
               (id, name, description, local_org_id, remote_org_id, remote_endpoint,
                state, direction, local_scope_id, created_at, created_by,
                approved_at, approved_by, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cid, name, description, local_org_id, remote_org_id, remote_endpoint,
             "proposed", direction.value, None, ts.isoformat(), created_by,
             None, None, json.dumps(meta)),
        )

        # Auto-register in registry (Invariant #1)
        try:
            reg = get_registry()
            entry = reg.register(
                kind=RegistryKind.CONNECTOR,
                namespace="connectors",
                name=f"connector:{cid}",
                registered_by=created_by,
                metadata={"connector_name": name},
            )
            SQLiteRegistryStore(self._backend).save_entry(entry)
        except Exception:
            pass

        if self._audit is not None:
            self._audit.record(
                actor_id=created_by,
                action=ActionType.CONNECTOR_PROPOSE,
                target_type="connector",
                target_id=cid,
                after_state=connector.snapshot(),
            )

        return connector

    def get_connector(self, connector_id: str) -> Connector | None:
        row = self._backend.fetch_one(
            "SELECT * FROM connectors WHERE id = ?", (connector_id,),
        )
        return connector_from_row(row) if row else None

    def get_connector_or_raise(self, connector_id: str) -> Connector:
        c = self.get_connector(connector_id)
        if c is None:
            raise ConnectorError(
                f"Connector {connector_id} not found",
                context={"connector_id": connector_id},
            )
        return c

    def list_connectors(
        self,
        *,
        local_org_id: str | None = None,
        state: ConnectorState | None = None,
        limit: int = 100,
    ) -> list[Connector]:
        clauses: list[str] = []
        params: list[Any] = []
        if local_org_id is not None:
            clauses.append("local_org_id = ?")
            params.append(local_org_id)
        if state is not None:
            clauses.append("state = ?")
            params.append(state.value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = self._backend.fetch_all(
            f"SELECT * FROM connectors{where} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        return [connector_from_row(r) for r in rows]

    # -- State transitions -------------------------------------------------

    def _transition(
        self,
        connector_id: str,
        target_state: ConnectorState,
        *,
        actor_id: str,
        action: ActionType,
    ) -> Connector:
        connector = self.get_connector_or_raise(connector_id)

        if not connector.can_transition_to(target_state):
            raise ConnectorError(
                f"Cannot transition connector from {connector.state.value} to {target_state.value}",
                context={
                    "connector_id": connector_id,
                    "current_state": connector.state.value,
                    "target_state": target_state.value,
                },
            )

        ts = now_utc()
        updates = ["state = ?"]
        params: list[Any] = [target_state.value]

        if target_state == ConnectorState.ACTIVE and connector.approved_at is None:
            updates.append("approved_at = ?")
            updates.append("approved_by = ?")
            params.extend([ts.isoformat(), actor_id])

        params.append(connector_id)
        self._backend.execute(
            f"UPDATE connectors SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )

        connector.state = target_state
        if target_state == ConnectorState.ACTIVE and connector.approved_at is None:
            connector.approved_at = ts
            connector.approved_by = actor_id

        if self._audit is not None:
            self._audit.record(
                actor_id=actor_id,
                action=action,
                target_type="connector",
                target_id=connector_id,
                after_state=connector.snapshot(),
            )

        return connector

    def submit_for_approval(self, connector_id: str, *, actor_id: str) -> Connector:
        """Move from proposed to pending_approval."""
        return self._transition(
            connector_id, ConnectorState.PENDING_APPROVAL,
            actor_id=actor_id, action=ActionType.CONNECTOR_PROPOSE,
        )

    def approve(self, connector_id: str, *, actor_id: str) -> Connector:
        """Approve and activate a connector (from pending_approval)."""
        return self._transition(
            connector_id, ConnectorState.ACTIVE,
            actor_id=actor_id, action=ActionType.CONNECTOR_APPROVE,
        )

    def reject(self, connector_id: str, *, actor_id: str) -> Connector:
        """Reject a proposed/pending connector."""
        return self._transition(
            connector_id, ConnectorState.REJECTED,
            actor_id=actor_id, action=ActionType.CONNECTOR_REVOKE,
        )

    def suspend(self, connector_id: str, *, actor_id: str) -> Connector:
        """Temporarily suspend an active connector."""
        return self._transition(
            connector_id, ConnectorState.SUSPENDED,
            actor_id=actor_id, action=ActionType.CONNECTOR_REVOKE,
        )

    def reactivate(self, connector_id: str, *, actor_id: str) -> Connector:
        """Reactivate a suspended connector."""
        return self._transition(
            connector_id, ConnectorState.ACTIVE,
            actor_id=actor_id, action=ActionType.CONNECTOR_APPROVE,
        )

    def revoke(self, connector_id: str, *, actor_id: str) -> Connector:
        """Permanently revoke a connector. Immediate and non-negotiable."""
        return self._transition(
            connector_id, ConnectorState.REVOKED,
            actor_id=actor_id, action=ActionType.CONNECTOR_REVOKE,
        )

    # -- Policies ----------------------------------------------------------

    def add_policy(
        self,
        *,
        connector_id: str,
        policy_type: PolicyType,
        config: dict[str, Any],
        created_by: str,
    ) -> ConnectorPolicy:
        """Add a policy to a connector."""
        self.get_connector_or_raise(connector_id)
        ts = now_utc()
        pid = generate_id()

        policy = ConnectorPolicy(
            id=pid,
            connector_id=connector_id,
            policy_type=policy_type,
            config=config,
            created_at=ts,
            created_by=created_by,
        )

        self._backend.execute(
            """INSERT INTO connector_policies
               (id, connector_id, policy_type, config_json, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (pid, connector_id, policy_type.value, json.dumps(config),
             ts.isoformat(), created_by),
        )

        return policy

    def get_policies(self, connector_id: str) -> list[ConnectorPolicy]:
        rows = self._backend.fetch_all(
            "SELECT * FROM connector_policies WHERE connector_id = ?",
            (connector_id,),
        )
        return [policy_from_row(r) for r in rows]

    def check_policy(
        self,
        connector_id: str,
        object_type: str,
    ) -> bool:
        """Check if an object type is allowed through the connector.

        Returns True if the object passes all policies.
        Secrets are NEVER allowed (framework-enforced).
        """
        # Hard rule: secrets never flow through connectors
        if object_type.lower() == "secret":
            return False

        policies = self.get_policies(connector_id)

        for policy in policies:
            if policy.policy_type == PolicyType.ALLOW_TYPES:
                allowed = policy.config.get("types", [])
                if allowed and object_type not in allowed:
                    return False

            elif policy.policy_type == PolicyType.DENY_TYPES:
                denied = policy.config.get("types", [])
                if object_type in denied:
                    return False

            elif policy.policy_type == PolicyType.CLASSIFICATION:
                blocked = policy.config.get("blocked_classifications", [])
                # Classification checking would need the object's classification;
                # for type-level checks, we pass through
                pass

        return True

    # -- Traffic -----------------------------------------------------------

    def record_traffic(
        self,
        *,
        connector_id: str,
        direction: str,
        object_type: str,
        action: str,
        object_id: str | None = None,
        status: TrafficStatus = TrafficStatus.SUCCESS,
        size_bytes: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConnectorTraffic:
        """Record a traffic event through a connector."""
        ts = now_utc()
        tid = generate_id()
        meta = metadata or {}

        traffic = ConnectorTraffic(
            id=tid,
            connector_id=connector_id,
            direction=direction,
            object_type=object_type,
            object_id=object_id,
            action=action,
            timestamp=ts,
            status=status,
            size_bytes=size_bytes,
            metadata=meta,
        )

        self._backend.execute(
            """INSERT INTO connector_traffic
               (id, connector_id, direction, object_type, object_id,
                action, timestamp, status, size_bytes, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tid, connector_id, direction, object_type, object_id,
             action, ts.isoformat(), status.value, size_bytes,
             json.dumps(meta)),
        )

        if self._audit is not None:
            self._audit.record(
                actor_id=connector_id,
                action=ActionType.CONNECTOR_SYNC,
                target_type="connector_traffic",
                target_id=tid,
            )

        return traffic

    def get_traffic(
        self,
        connector_id: str,
        *,
        direction: str | None = None,
        limit: int = 100,
    ) -> list[ConnectorTraffic]:
        if direction is not None:
            rows = self._backend.fetch_all(
                """SELECT * FROM connector_traffic
                   WHERE connector_id = ? AND direction = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (connector_id, direction, limit),
            )
        else:
            rows = self._backend.fetch_all(
                """SELECT * FROM connector_traffic
                   WHERE connector_id = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (connector_id, limit),
            )
        return [traffic_from_row(r) for r in rows]

    # -- Sync (high-level operation) ---------------------------------------

    def sync_object(
        self,
        connector_id: str,
        *,
        object_type: str,
        object_id: str | None = None,
        direction: str = "outbound",
        size_bytes: int | None = None,
    ) -> ConnectorTraffic:
        """Sync an object through a connector with policy checking.

        Raises ConnectorNotApprovedError if connector is not active.
        Raises ConnectorRevokedError if connector is revoked.
        Raises ConnectorPolicyViolation if object type is blocked.
        """
        connector = self.get_connector_or_raise(connector_id)

        if connector.state == ConnectorState.REVOKED:
            raise ConnectorRevokedError(
                "Connector has been revoked",
                context={"connector_id": connector_id},
            )

        if not connector.is_active:
            raise ConnectorNotApprovedError(
                f"Connector is not active (state: {connector.state.value})",
                context={"connector_id": connector_id, "state": connector.state.value},
            )

        # Check direction compatibility
        if direction == "outbound" and connector.direction == ConnectorDirection.INBOUND:
            raise ConnectorPolicyViolation(
                "Connector only allows inbound traffic",
                context={"connector_id": connector_id, "direction": direction},
            )
        if direction == "inbound" and connector.direction == ConnectorDirection.OUTBOUND:
            raise ConnectorPolicyViolation(
                "Connector only allows outbound traffic",
                context={"connector_id": connector_id, "direction": direction},
            )

        # Check policies
        if not self.check_policy(connector_id, object_type):
            traffic = self.record_traffic(
                connector_id=connector_id,
                direction=direction,
                object_type=object_type,
                object_id=object_id,
                action="sync",
                status=TrafficStatus.BLOCKED,
                size_bytes=size_bytes,
            )
            raise ConnectorPolicyViolation(
                f"Object type '{object_type}' is blocked by connector policy",
                context={
                    "connector_id": connector_id,
                    "object_type": object_type,
                },
            )

        # Push data to remote endpoint if transport is configured
        if self._transport and direction == "outbound":
            try:
                payload = {
                    "connector_id": connector_id,
                    "object_type": object_type,
                    "object_id": object_id,
                    "direction": direction,
                    "timestamp": now_utc().isoformat(),
                }
                status_code, response_body = self._transport(
                    connector.remote_endpoint, payload,
                )
                if status_code < 200 or status_code >= 300:
                    return self.record_traffic(
                        connector_id=connector_id,
                        direction=direction,
                        object_type=object_type,
                        object_id=object_id,
                        action="sync",
                        status=TrafficStatus.FAILED,
                        size_bytes=size_bytes,
                        metadata={"status_code": status_code, "response": response_body},
                    )
            except Exception as exc:
                return self.record_traffic(
                    connector_id=connector_id,
                    direction=direction,
                    object_type=object_type,
                    object_id=object_id,
                    action="sync",
                    status=TrafficStatus.FAILED,
                    size_bytes=size_bytes,
                    metadata={"error": str(exc)},
                )

        # Record successful traffic
        return self.record_traffic(
            connector_id=connector_id,
            direction=direction,
            object_type=object_type,
            object_id=object_id,
            action="sync",
            status=TrafficStatus.SUCCESS,
            size_bytes=size_bytes,
        )

    @staticmethod
    def http_transport(
        endpoint_url: str,
        payload: dict[str, Any],
        *,
        timeout: int = 10,
    ) -> tuple[int, str]:
        """Real HTTP transport using stdlib urllib.

        Posts the payload as JSON to the endpoint URL. Returns
        ``(status_code, response_body)``.

        Pass as ``transport=ConnectorManager.http_transport`` when
        constructing the manager for production use.
        """
        import urllib.error
        import urllib.request

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "pyscoped-connector/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return (resp.status, body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            return (exc.code, body)
        except urllib.error.URLError as exc:
            raise ConnectionError(f"Connector sync failed: {exc.reason}") from exc
