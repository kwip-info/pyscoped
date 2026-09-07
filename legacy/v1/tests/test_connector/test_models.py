"""Tests for connector and marketplace data models."""

from datetime import datetime, timezone

import pytest

from scoped.connector.models import (
    Connector,
    ConnectorDirection,
    ConnectorPolicy,
    ConnectorState,
    ConnectorTraffic,
    PolicyType,
    TrafficStatus,
    TERMINAL_CONNECTOR_STATES,
    VALID_CONNECTOR_TRANSITIONS,
    connector_from_row,
    policy_from_row,
    traffic_from_row,
)
from scoped.connector.marketplace.models import (
    ListingType,
    MarketplaceInstall,
    MarketplaceListing,
    MarketplaceReview,
    Visibility,
    listing_from_row,
    review_from_row,
    install_from_row,
)
from scoped.types import Lifecycle


class TestConnector:

    def test_defaults(self):
        ts = datetime.now(timezone.utc)
        c = Connector(
            id="c1", name="acme-bridge", local_org_id="org1",
            remote_org_id="org2", remote_endpoint="https://org2.example.com",
            created_at=ts, created_by="alice",
        )
        assert c.state == ConnectorState.PROPOSED
        assert c.direction == ConnectorDirection.BIDIRECTIONAL
        assert not c.is_active
        assert not c.is_terminal

    def test_transitions(self):
        ts = datetime.now(timezone.utc)
        c = Connector(
            id="c1", name="test", local_org_id="org1",
            remote_org_id="org2", remote_endpoint="https://example.com",
            created_at=ts, created_by="alice",
        )
        assert c.can_transition_to(ConnectorState.PENDING_APPROVAL)
        assert not c.can_transition_to(ConnectorState.ACTIVE)  # must go through pending

    def test_active_transitions(self):
        ts = datetime.now(timezone.utc)
        c = Connector(
            id="c1", name="test", local_org_id="org1",
            remote_org_id="org2", remote_endpoint="https://example.com",
            created_at=ts, created_by="alice",
            state=ConnectorState.ACTIVE,
        )
        assert c.is_active
        assert c.can_transition_to(ConnectorState.SUSPENDED)
        assert c.can_transition_to(ConnectorState.REVOKED)
        assert not c.can_transition_to(ConnectorState.PROPOSED)

    def test_terminal_states(self):
        assert ConnectorState.REVOKED in TERMINAL_CONNECTOR_STATES
        assert ConnectorState.REJECTED in TERMINAL_CONNECTOR_STATES
        assert ConnectorState.ACTIVE not in TERMINAL_CONNECTOR_STATES

    def test_revoked_no_transitions(self):
        ts = datetime.now(timezone.utc)
        c = Connector(
            id="c1", name="test", local_org_id="org1",
            remote_org_id="org2", remote_endpoint="https://example.com",
            created_at=ts, created_by="alice",
            state=ConnectorState.REVOKED,
        )
        assert c.is_terminal
        assert not c.can_transition_to(ConnectorState.ACTIVE)

    def test_snapshot(self):
        ts = datetime.now(timezone.utc)
        c = Connector(
            id="c1", name="bridge", local_org_id="org1",
            remote_org_id="org2", remote_endpoint="https://example.com",
            created_at=ts, created_by="alice",
            metadata={"region": "us-east"},
        )
        snap = c.snapshot()
        assert snap["name"] == "bridge"
        assert snap["state"] == "proposed"
        assert snap["metadata"] == {"region": "us-east"}

    def test_from_row(self):
        row = {
            "id": "c1", "name": "bridge", "description": "Test",
            "local_org_id": "org1", "remote_org_id": "org2",
            "remote_endpoint": "https://example.com",
            "state": "active", "direction": "outbound",
            "local_scope_id": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "created_by": "alice",
            "approved_at": "2026-01-02T00:00:00+00:00",
            "approved_by": "bob",
            "metadata_json": '{"key": "val"}',
        }
        c = connector_from_row(row)
        assert c.state == ConnectorState.ACTIVE
        assert c.direction == ConnectorDirection.OUTBOUND
        assert c.approved_by == "bob"


class TestConnectorPolicy:

    def test_snapshot(self):
        ts = datetime.now(timezone.utc)
        p = ConnectorPolicy(
            id="p1", connector_id="c1",
            policy_type=PolicyType.ALLOW_TYPES,
            config={"types": ["Document", "Report"]},
            created_at=ts, created_by="alice",
        )
        snap = p.snapshot()
        assert snap["policy_type"] == "allow_types"
        assert snap["config"]["types"] == ["Document", "Report"]

    def test_from_row(self):
        row = {
            "id": "p1", "connector_id": "c1",
            "policy_type": "deny_types",
            "config_json": '{"types": ["Secret"]}',
            "created_at": "2026-01-01T00:00:00+00:00",
            "created_by": "alice",
        }
        p = policy_from_row(row)
        assert p.policy_type == PolicyType.DENY_TYPES
        assert p.config == {"types": ["Secret"]}


class TestConnectorTraffic:

    def test_from_row(self):
        row = {
            "id": "t1", "connector_id": "c1",
            "direction": "outbound", "object_type": "Document",
            "object_id": "doc1", "action": "sync",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "status": "success", "size_bytes": 1024,
            "metadata_json": "{}",
        }
        t = traffic_from_row(row)
        assert t.direction == "outbound"
        assert t.status == TrafficStatus.SUCCESS
        assert t.size_bytes == 1024


class TestMarketplaceListing:

    def test_defaults(self):
        ts = datetime.now(timezone.utc)
        l = MarketplaceListing(
            id="l1", name="Test Plugin", publisher_id="alice",
            listing_type=ListingType.PLUGIN, published_at=ts,
        )
        assert l.is_active
        assert l.is_public
        assert l.download_count == 0

    def test_snapshot(self):
        ts = datetime.now(timezone.utc)
        l = MarketplaceListing(
            id="l1", name="Plugin", publisher_id="alice",
            listing_type=ListingType.PLUGIN, published_at=ts,
            config_template={"key": "val"},
        )
        snap = l.snapshot()
        assert snap["listing_type"] == "plugin"
        assert snap["config_template"] == {"key": "val"}

    def test_from_row(self):
        row = {
            "id": "l1", "name": "Test", "description": "A test",
            "publisher_id": "alice", "listing_type": "connector_template",
            "version": "2.0.0", "config_template": '{"x": 1}',
            "visibility": "unlisted",
            "published_at": "2026-01-01T00:00:00+00:00",
            "updated_at": None, "lifecycle": "ACTIVE",
            "download_count": 5, "metadata_json": "{}",
        }
        l = listing_from_row(row)
        assert l.listing_type == ListingType.CONNECTOR_TEMPLATE
        assert l.visibility == Visibility.UNLISTED
        assert l.download_count == 5


class TestMarketplaceReview:

    def test_from_row(self):
        row = {
            "id": "r1", "listing_id": "l1", "reviewer_id": "bob",
            "rating": 4, "review_text": "Great!",
            "reviewed_at": "2026-01-01T00:00:00+00:00",
        }
        r = review_from_row(row)
        assert r.rating == 4
        assert r.review_text == "Great!"


class TestMarketplaceInstall:

    def test_from_row(self):
        row = {
            "id": "i1", "listing_id": "l1", "installer_id": "charlie",
            "installed_at": "2026-01-01T00:00:00+00:00",
            "version": "1.0.0", "config_json": '{"k": "v"}',
            "result_ref": "plugin-123", "result_type": "plugin",
        }
        i = install_from_row(row)
        assert i.config == {"k": "v"}
        assert i.result_ref == "plugin-123"
        assert i.result_type == "plugin"
