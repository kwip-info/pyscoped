"""Tests for connector bridge — CRUD, state transitions, policies, traffic."""

import pytest

from scoped.connector.bridge import ConnectorManager
from scoped.connector.models import (
    ConnectorDirection,
    ConnectorState,
    PolicyType,
    TrafficStatus,
)
from scoped.exceptions import (
    ConnectorError,
    ConnectorNotApprovedError,
    ConnectorPolicyViolation,
    ConnectorRevokedError,
)
from scoped.identity.principal import PrincipalStore


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    org1 = store.create_principal(kind="org", display_name="Acme Corp", principal_id="org1")
    org2 = store.create_principal(kind="org", display_name="Beta Inc", principal_id="org2")
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    return org1, org2, alice


@pytest.fixture
def manager(sqlite_backend):
    return ConnectorManager(sqlite_backend)


class TestPropose:

    def test_basic_propose(self, manager, principals):
        org1, org2, alice = principals
        c = manager.propose(
            name="acme-beta", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://beta.example.com",
            created_by=alice.id,
        )
        assert c.state == ConnectorState.PROPOSED
        assert c.name == "acme-beta"
        assert c.direction == ConnectorDirection.BIDIRECTIONAL

    def test_propose_with_direction(self, manager, principals):
        org1, org2, alice = principals
        c = manager.propose(
            name="outbound-only", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://beta.example.com",
            created_by=alice.id, direction=ConnectorDirection.OUTBOUND,
        )
        assert c.direction == ConnectorDirection.OUTBOUND

    def test_propose_with_metadata(self, manager, principals):
        org1, org2, alice = principals
        c = manager.propose(
            name="meta", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://example.com",
            created_by=alice.id, metadata={"purpose": "data sync"},
        )
        assert c.metadata["purpose"] == "data sync"


class TestGetConnector:

    def test_get_existing(self, manager, principals):
        org1, org2, alice = principals
        created = manager.propose(
            name="test", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://example.com",
            created_by=alice.id,
        )
        fetched = manager.get_connector(created.id)
        assert fetched is not None
        assert fetched.id == created.id

    def test_get_nonexistent(self, manager):
        assert manager.get_connector("nope") is None

    def test_get_or_raise(self, manager):
        with pytest.raises(ConnectorError, match="not found"):
            manager.get_connector_or_raise("nope")


class TestListConnectors:

    def test_list_by_org(self, manager, principals):
        org1, org2, alice = principals
        manager.propose(
            name="c1", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://example.com",
            created_by=alice.id,
        )
        result = manager.list_connectors(local_org_id=org1.id)
        assert len(result) == 1

    def test_list_by_state(self, manager, principals):
        org1, org2, alice = principals
        c = manager.propose(
            name="c1", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://example.com",
            created_by=alice.id,
        )
        manager.submit_for_approval(c.id, actor_id=alice.id)
        result = manager.list_connectors(state=ConnectorState.PENDING_APPROVAL)
        assert len(result) == 1


class TestStateTransitions:

    def test_full_lifecycle(self, manager, principals):
        org1, org2, alice = principals
        c = manager.propose(
            name="lifecycle", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://example.com",
            created_by=alice.id,
        )
        assert c.state == ConnectorState.PROPOSED

        c = manager.submit_for_approval(c.id, actor_id=alice.id)
        assert c.state == ConnectorState.PENDING_APPROVAL

        c = manager.approve(c.id, actor_id=alice.id)
        assert c.state == ConnectorState.ACTIVE
        assert c.approved_at is not None
        assert c.approved_by == alice.id

    def test_suspend_and_reactivate(self, manager, principals):
        org1, org2, alice = principals
        c = manager.propose(
            name="suspend-test", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://example.com",
            created_by=alice.id,
        )
        manager.submit_for_approval(c.id, actor_id=alice.id)
        manager.approve(c.id, actor_id=alice.id)

        c = manager.suspend(c.id, actor_id=alice.id)
        assert c.state == ConnectorState.SUSPENDED

        c = manager.reactivate(c.id, actor_id=alice.id)
        assert c.state == ConnectorState.ACTIVE

    def test_revoke_is_permanent(self, manager, principals):
        org1, org2, alice = principals
        c = manager.propose(
            name="revoke-test", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://example.com",
            created_by=alice.id,
        )
        manager.submit_for_approval(c.id, actor_id=alice.id)
        manager.approve(c.id, actor_id=alice.id)

        c = manager.revoke(c.id, actor_id=alice.id)
        assert c.state == ConnectorState.REVOKED
        assert c.is_terminal

        with pytest.raises(ConnectorError, match="Cannot transition"):
            manager.reactivate(c.id, actor_id=alice.id)

    def test_reject_proposed(self, manager, principals):
        org1, org2, alice = principals
        c = manager.propose(
            name="reject-test", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://example.com",
            created_by=alice.id,
        )
        c = manager.reject(c.id, actor_id=alice.id)
        assert c.state == ConnectorState.REJECTED
        assert c.is_terminal

    def test_invalid_transition(self, manager, principals):
        org1, org2, alice = principals
        c = manager.propose(
            name="invalid", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://example.com",
            created_by=alice.id,
        )
        # Can't go directly from proposed to active
        with pytest.raises(ConnectorError, match="Cannot transition"):
            manager.approve(c.id, actor_id=alice.id)

    def test_revoke_from_any_non_terminal(self, manager, principals):
        org1, org2, alice = principals
        # Revoke from proposed
        c = manager.propose(
            name="revoke-proposed", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://example.com",
            created_by=alice.id,
        )
        c = manager.revoke(c.id, actor_id=alice.id)
        assert c.state == ConnectorState.REVOKED


class TestPolicies:

    def _active_connector(self, manager, principals):
        org1, org2, alice = principals
        c = manager.propose(
            name="policy-test", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://example.com",
            created_by=alice.id,
        )
        manager.submit_for_approval(c.id, actor_id=alice.id)
        manager.approve(c.id, actor_id=alice.id)
        return c

    def test_add_policy(self, manager, principals):
        _, _, alice = principals
        c = self._active_connector(manager, principals)
        p = manager.add_policy(
            connector_id=c.id,
            policy_type=PolicyType.ALLOW_TYPES,
            config={"types": ["Document", "Report"]},
            created_by=alice.id,
        )
        assert p.policy_type == PolicyType.ALLOW_TYPES
        assert p.config["types"] == ["Document", "Report"]

    def test_get_policies(self, manager, principals):
        _, _, alice = principals
        c = self._active_connector(manager, principals)
        manager.add_policy(
            connector_id=c.id, policy_type=PolicyType.ALLOW_TYPES,
            config={"types": ["Document"]}, created_by=alice.id,
        )
        manager.add_policy(
            connector_id=c.id, policy_type=PolicyType.DENY_TYPES,
            config={"types": ["InternalMemo"]}, created_by=alice.id,
        )
        policies = manager.get_policies(c.id)
        assert len(policies) == 2

    def test_check_policy_allow(self, manager, principals):
        _, _, alice = principals
        c = self._active_connector(manager, principals)
        manager.add_policy(
            connector_id=c.id, policy_type=PolicyType.ALLOW_TYPES,
            config={"types": ["Document", "Report"]}, created_by=alice.id,
        )
        assert manager.check_policy(c.id, "Document") is True
        assert manager.check_policy(c.id, "Unknown") is False

    def test_check_policy_deny(self, manager, principals):
        _, _, alice = principals
        c = self._active_connector(manager, principals)
        manager.add_policy(
            connector_id=c.id, policy_type=PolicyType.DENY_TYPES,
            config={"types": ["InternalMemo"]}, created_by=alice.id,
        )
        assert manager.check_policy(c.id, "Document") is True
        assert manager.check_policy(c.id, "InternalMemo") is False

    def test_secrets_never_flow(self, manager, principals):
        c = self._active_connector(manager, principals)
        # Even with no deny policy, secrets are blocked
        assert manager.check_policy(c.id, "secret") is False
        assert manager.check_policy(c.id, "Secret") is False

    def test_no_policies_allows_all(self, manager, principals):
        c = self._active_connector(manager, principals)
        # No policies — everything except secrets passes
        assert manager.check_policy(c.id, "Document") is True
        assert manager.check_policy(c.id, "anything") is True


class TestTraffic:

    def _active_connector(self, manager, principals):
        org1, org2, alice = principals
        c = manager.propose(
            name="traffic-test", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://example.com",
            created_by=alice.id,
        )
        manager.submit_for_approval(c.id, actor_id=alice.id)
        manager.approve(c.id, actor_id=alice.id)
        return c

    def test_record_traffic(self, manager, principals):
        c = self._active_connector(manager, principals)
        t = manager.record_traffic(
            connector_id=c.id, direction="outbound",
            object_type="Document", action="sync",
            size_bytes=2048,
        )
        assert t.direction == "outbound"
        assert t.status == TrafficStatus.SUCCESS

    def test_get_traffic(self, manager, principals):
        c = self._active_connector(manager, principals)
        manager.record_traffic(
            connector_id=c.id, direction="outbound",
            object_type="Document", action="sync",
        )
        manager.record_traffic(
            connector_id=c.id, direction="inbound",
            object_type="Report", action="sync",
        )
        all_traffic = manager.get_traffic(c.id)
        assert len(all_traffic) == 2

    def test_get_traffic_by_direction(self, manager, principals):
        c = self._active_connector(manager, principals)
        manager.record_traffic(
            connector_id=c.id, direction="outbound",
            object_type="Document", action="sync",
        )
        manager.record_traffic(
            connector_id=c.id, direction="inbound",
            object_type="Report", action="sync",
        )
        outbound = manager.get_traffic(c.id, direction="outbound")
        assert len(outbound) == 1
        assert outbound[0].direction == "outbound"


class TestSyncObject:

    def _active_connector(self, manager, principals):
        org1, org2, alice = principals
        c = manager.propose(
            name="sync-test", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://example.com",
            created_by=alice.id,
        )
        manager.submit_for_approval(c.id, actor_id=alice.id)
        manager.approve(c.id, actor_id=alice.id)
        return c

    def test_sync_success(self, manager, principals):
        c = self._active_connector(manager, principals)
        t = manager.sync_object(
            c.id, object_type="Document", direction="outbound",
        )
        assert t.status == TrafficStatus.SUCCESS

    def test_sync_not_active(self, manager, principals):
        org1, org2, alice = principals
        c = manager.propose(
            name="not-active", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://example.com",
            created_by=alice.id,
        )
        with pytest.raises(ConnectorNotApprovedError, match="not active"):
            manager.sync_object(c.id, object_type="Document")

    def test_sync_revoked(self, manager, principals):
        _, _, alice = principals
        c = self._active_connector(manager, principals)
        manager.revoke(c.id, actor_id=alice.id)
        with pytest.raises(ConnectorRevokedError, match="revoked"):
            manager.sync_object(c.id, object_type="Document")

    def test_sync_blocked_by_policy(self, manager, principals):
        _, _, alice = principals
        c = self._active_connector(manager, principals)
        manager.add_policy(
            connector_id=c.id, policy_type=PolicyType.DENY_TYPES,
            config={"types": ["InternalMemo"]}, created_by=alice.id,
        )
        with pytest.raises(ConnectorPolicyViolation, match="blocked"):
            manager.sync_object(c.id, object_type="InternalMemo")

    def test_sync_secrets_always_blocked(self, manager, principals):
        c = self._active_connector(manager, principals)
        with pytest.raises(ConnectorPolicyViolation, match="blocked"):
            manager.sync_object(c.id, object_type="secret")

    def test_sync_wrong_direction_outbound(self, manager, principals):
        org1, org2, alice = principals
        c = manager.propose(
            name="inbound-only", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://example.com",
            created_by=alice.id, direction=ConnectorDirection.INBOUND,
        )
        manager.submit_for_approval(c.id, actor_id=alice.id)
        manager.approve(c.id, actor_id=alice.id)

        with pytest.raises(ConnectorPolicyViolation, match="inbound"):
            manager.sync_object(c.id, object_type="Document", direction="outbound")

    def test_sync_wrong_direction_inbound(self, manager, principals):
        org1, org2, alice = principals
        c = manager.propose(
            name="outbound-only", local_org_id=org1.id,
            remote_org_id=org2.id, remote_endpoint="https://example.com",
            created_by=alice.id, direction=ConnectorDirection.OUTBOUND,
        )
        manager.submit_for_approval(c.id, actor_id=alice.id)
        manager.approve(c.id, actor_id=alice.id)

        with pytest.raises(ConnectorPolicyViolation, match="outbound"):
            manager.sync_object(c.id, object_type="Document", direction="inbound")

    def test_blocked_sync_records_traffic(self, manager, principals):
        _, _, alice = principals
        c = self._active_connector(manager, principals)
        manager.add_policy(
            connector_id=c.id, policy_type=PolicyType.DENY_TYPES,
            config={"types": ["Blocked"]}, created_by=alice.id,
        )
        try:
            manager.sync_object(c.id, object_type="Blocked")
        except ConnectorPolicyViolation:
            pass
        traffic = manager.get_traffic(c.id)
        assert len(traffic) == 1
        assert traffic[0].status == TrafficStatus.BLOCKED
