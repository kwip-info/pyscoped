"""Tests for Layer 13 namespaces (connectors, marketplace) and rule gating."""

import pytest

import scoped
from scoped.connector.models import (
    ConnectorDirection,
    ConnectorState,
    PolicyType,
    TrafficStatus,
)
from scoped.connector.marketplace.models import ListingType, Visibility
from scoped.exceptions import AccessDeniedError
from scoped.rules.engine import RuleStore
from scoped.rules.models import BindingTargetType, RuleEffect, RuleType


@pytest.fixture
def client():
    return scoped.ScopedClient()


@pytest.fixture
def org1(client):
    return client.principals.create("Org1", kind="org", principal_id="org1")


@pytest.fixture
def org2(client):
    return client.principals.create("Org2", kind="org", principal_id="org2")


@pytest.fixture
def alice(client):
    return client.principals.create("Alice", principal_id="alice")


@pytest.fixture
def bob(client):
    return client.principals.create("Bob", principal_id="bob")


# ---------------------------------------------------------------------------
# Service wiring
# ---------------------------------------------------------------------------

class TestServicesWiring:

    def test_services_exposes_connectors(self, client):
        from scoped.connector.bridge import ConnectorManager
        assert isinstance(client.services.connectors, ConnectorManager)

    def test_services_exposes_marketplace_publisher(self, client):
        from scoped.connector.marketplace.publishing import MarketplacePublisher
        assert isinstance(
            client.services.marketplace_publisher, MarketplacePublisher,
        )

    def test_services_exposes_marketplace_discovery(self, client):
        from scoped.connector.marketplace.discovery import MarketplaceDiscovery
        assert isinstance(
            client.services.marketplace_discovery, MarketplaceDiscovery,
        )

    def test_services_federation_factory_returns_protocol(self, client):
        from scoped.connector.protocol import FederationProtocol

        proto = client.services.federation_protocol("k1")
        assert isinstance(proto, FederationProtocol)
        # Distinct keys → distinct instances (no caching).
        proto2 = client.services.federation_protocol("k2")
        assert proto is not proto2


# ---------------------------------------------------------------------------
# ConnectorsNamespace
# ---------------------------------------------------------------------------

class TestConnectorsNamespace:

    def test_full_lifecycle_with_inferred_actor(
        self, client, alice, org1, org2,
    ):
        with client.as_principal(alice):
            c = client.connectors.propose(
                name="c1",
                local_org_id=org1, remote_org_id=org2,
                remote_endpoint="https://example.com/sync",
            )
            assert c.state == ConnectorState.PROPOSED

            c = client.connectors.submit(c)
            assert c.state == ConnectorState.PENDING_APPROVAL

            c = client.connectors.approve(c)
            assert c.state == ConnectorState.ACTIVE
            assert c.approved_by == alice.id

            c = client.connectors.suspend(c)
            assert c.state == ConnectorState.SUSPENDED

            c = client.connectors.reactivate(c)
            assert c.state == ConnectorState.ACTIVE

    def test_add_policy_and_sync(self, client, alice, org1, org2):
        with client.as_principal(alice):
            c = client.connectors.propose(
                name="c1",
                local_org_id=org1, remote_org_id=org2,
                remote_endpoint="https://example.com/sync",
            )
            client.connectors.submit(c)
            client.connectors.approve(c)

            client.connectors.add_policy(
                c, policy_type=PolicyType.ALLOW_TYPES,
                config={"types": ["Document"]},
            )
            assert client.connectors.check_policy(c, "Document") is True
            assert client.connectors.check_policy(c, "InternalMemo") is False

            traffic = client.connectors.sync(
                c, object_type="Document", object_id="d1",
            )
            assert traffic.status == TrafficStatus.SUCCESS

    def test_owner_enforcement_through_namespace(
        self, client, alice, bob, org1, org2,
    ):
        with client.as_principal(alice):
            c = client.connectors.propose(
                name="c1",
                local_org_id=org1, remote_org_id=org2,
                remote_endpoint="https://example.com/sync",
            )
        with client.as_principal(bob):
            with pytest.raises(AccessDeniedError, match="not the creator"):
                client.connectors.submit(c)

    def test_federation_factory_through_namespace(self, client):
        from scoped.connector.protocol import FederationProtocol
        proto = client.connectors.federation("shared-key")
        assert isinstance(proto, FederationProtocol)


# ---------------------------------------------------------------------------
# MarketplaceNamespace
# ---------------------------------------------------------------------------

class TestMarketplaceNamespace:

    def test_publish_and_query(self, client, alice):
        with client.as_principal(alice):
            listing = client.marketplace.publish(
                "My Plugin", listing_type=ListingType.PLUGIN,
            )
        assert listing.publisher_id == alice.id
        assert client.marketplace.get(listing.id).id == listing.id

    def test_publisher_lifecycle(self, client, alice):
        with client.as_principal(alice):
            listing = client.marketplace.publish(
                "Lifecycle", listing_type=ListingType.PLUGIN, version="1.0.0",
            )
            updated = client.marketplace.update_version(
                listing, new_version="1.1.0",
            )
            assert updated.version == "1.1.0"

            client.marketplace.deprecate(listing)
            client.marketplace.update_visibility(
                listing, visibility=Visibility.UNLISTED,
            )

    def test_browse_search_install(self, client, alice, bob):
        with client.as_principal(alice):
            listing = client.marketplace.publish(
                "Discoverable", listing_type=ListingType.PLUGIN,
                description="searchable description",
            )

        with client.as_principal(bob):
            results = client.marketplace.browse(
                listing_type=ListingType.PLUGIN,
            )
            assert any(l.id == listing.id for l in results)

            search_hits = client.marketplace.search("searchable")
            assert any(l.id == listing.id for l in search_hits)

            install = client.marketplace.install(listing)
            assert install.installer_id == bob.id

    def test_review_inferred_reviewer(self, client, alice, bob):
        with client.as_principal(alice):
            listing = client.marketplace.publish(
                "Reviewable", listing_type=ListingType.PLUGIN,
            )
        with client.as_principal(bob):
            review = client.marketplace.review(
                listing, rating=5, review_text="great",
            )
        assert review.reviewer_id == bob.id

    def test_owner_enforcement_through_namespace(self, client, alice, bob):
        with client.as_principal(alice):
            listing = client.marketplace.publish(
                "Mine", listing_type=ListingType.PLUGIN,
            )
        with client.as_principal(bob):
            with pytest.raises(AccessDeniedError, match="not the publisher"):
                client.marketplace.deprecate(listing)


# ---------------------------------------------------------------------------
# Rule engine integration
# ---------------------------------------------------------------------------

class TestRuleGating:

    def _bind_deny(self, client, *, action, target_type, target_id, name):
        store = RuleStore(client.services.backend)
        rule = store.create_rule(
            name=name,
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY,
            conditions={"action": action},
            priority=100,
            created_by="system",
        )
        store.bind_rule(
            rule.id,
            target_type=target_type,
            target_id=target_id,
            bound_by="system",
        )
        # Invalidate cache so subsequent evaluate() sees the new rule.
        if client.services.rule_engine._cache is not None:
            client.services.rule_engine._cache.clear()

    def test_deny_blocks_connector_propose(
        self, client, alice, org1, org2,
    ):
        self._bind_deny(
            client,
            action="connector_propose",
            target_type=BindingTargetType.OBJECT_TYPE,
            target_id="connector",
            name="block-propose",
        )
        with client.as_principal(alice):
            with pytest.raises(AccessDeniedError, match="connector_propose"):
                client.connectors.propose(
                    name="blocked",
                    local_org_id=org1, remote_org_id=org2,
                    remote_endpoint="https://x",
                )

    def test_deny_blocks_connector_approve(
        self, client, alice, org1, org2,
    ):
        with client.as_principal(alice):
            c = client.connectors.propose(
                name="c1",
                local_org_id=org1, remote_org_id=org2,
                remote_endpoint="https://x",
            )
            client.connectors.submit(c)

        self._bind_deny(
            client,
            action="connector_approve",
            target_type=BindingTargetType.OBJECT_TYPE,
            target_id="connector",
            name="block-approve",
        )
        with client.as_principal(alice):
            with pytest.raises(AccessDeniedError, match="connector_approve"):
                client.connectors.approve(c)

    def test_deny_blocks_connector_sync(self, client, alice, org1, org2):
        with client.as_principal(alice):
            c = client.connectors.propose(
                name="c1",
                local_org_id=org1, remote_org_id=org2,
                remote_endpoint="https://x",
            )
            client.connectors.submit(c)
            client.connectors.approve(c)

        # Bind to the specific connector by ID.
        store = RuleStore(client.services.backend)
        rule = store.create_rule(
            name="block-sync",
            rule_type=RuleType.ACCESS,
            effect=RuleEffect.DENY,
            conditions={"action": "connector_sync"},
            priority=100,
            created_by="system",
        )
        store.bind_rule(
            rule.id,
            target_type=BindingTargetType.OBJECT,
            target_id=c.id,
            bound_by="system",
        )
        if client.services.rule_engine._cache is not None:
            client.services.rule_engine._cache.clear()

        with client.as_principal(alice):
            with pytest.raises(AccessDeniedError, match="connector_sync"):
                client.connectors.sync(c, object_type="Document")

    def test_deny_blocks_marketplace_publish(self, client, alice):
        self._bind_deny(
            client,
            action="marketplace_publish",
            target_type=BindingTargetType.OBJECT_TYPE,
            target_id="marketplace_listing",
            name="block-publish",
        )
        with client.as_principal(alice):
            with pytest.raises(AccessDeniedError, match="marketplace_publish"):
                client.marketplace.publish(
                    "Blocked", listing_type=ListingType.PLUGIN,
                )

    def test_deny_blocks_marketplace_install(self, client, alice, bob):
        with client.as_principal(alice):
            listing = client.marketplace.publish(
                "Risky", listing_type=ListingType.PLUGIN,
            )

        self._bind_deny(
            client,
            action="marketplace_install",
            target_type=BindingTargetType.OBJECT_TYPE,
            target_id="marketplace_listing",
            name="block-install",
        )
        with client.as_principal(bob):
            with pytest.raises(AccessDeniedError, match="marketplace_install"):
                client.marketplace.install(listing)

    def test_no_rules_default_permit(self, client, alice, org1, org2):
        """Without rules, behavior is unchanged (default-permit)."""
        with client.as_principal(alice):
            c = client.connectors.propose(
                name="ungated",
                local_org_id=org1, remote_org_id=org2,
                remote_endpoint="https://x",
            )
        assert c.name == "ungated"


# ---------------------------------------------------------------------------
# Module-level proxies
# ---------------------------------------------------------------------------

class TestModuleLevelProxies:

    def test_module_namespaces_resolve(self):
        client = scoped.init()
        try:
            org1 = client.principals.create(
                "ModOrg1", kind="org", principal_id="modorg1",
            )
            org2 = client.principals.create(
                "ModOrg2", kind="org", principal_id="modorg2",
            )
            alice = client.principals.create("ModAlice", principal_id="modalice")
            with scoped.as_principal(alice):
                c = scoped.connectors.propose(
                    name="modconn",
                    local_org_id=org1, remote_org_id=org2,
                    remote_endpoint="https://x",
                )
                assert c.created_by == alice.id

                listing = scoped.marketplace.publish(
                    "ModListing", listing_type=ListingType.PLUGIN,
                )
                assert listing.publisher_id == alice.id
        finally:
            import scoped.client as _client_module

            _client_module._default_client = None
