"""Phase 1 hardening tests for Layer 13 (Connector + Marketplace).

Covers:
- ConnectorManager: creator-only state transitions and add_policy.
- ConnectorManager: actor_id threading + audit emission for traffic.
- ConnectorManager: stricter secret-leakage block (case + variant set).
- MarketplacePublisher: publisher-only mutations, audit emission, semver
  validation, lifecycle gate on update_version, duplicate-review wrap.
- MarketplaceDiscovery.install: visibility gate (PRIVATE = publisher-only)
  and corrected audit target.
- FederationProtocol: sender sequence persists across instances, and
  receiver-side replay detection.
"""

import pytest

from scoped.audit.query import AuditQuery
from scoped.audit.writer import AuditWriter
from scoped.connector.bridge import ConnectorManager
from scoped.connector.marketplace.discovery import MarketplaceDiscovery
from scoped.connector.marketplace.models import ListingType, Visibility
from scoped.connector.marketplace.publishing import MarketplacePublisher
from scoped.connector.models import ConnectorState, PolicyType, TrafficStatus
from scoped.connector.protocol import FederationProtocol
from scoped.exceptions import (
    AccessDeniedError,
    ConnectorPolicyViolation,
    FederationError,
    MarketplaceError,
)
from scoped.identity.principal import PrincipalStore
from scoped.types import ActionType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(sqlite_backend, registry):
    return PrincipalStore(sqlite_backend)


@pytest.fixture
def org1(store):
    return store.create_principal(kind="org", display_name="Acme", principal_id="org1")


@pytest.fixture
def org2(store):
    return store.create_principal(kind="org", display_name="Beta", principal_id="org2")


@pytest.fixture
def alice(store):
    return store.create_principal(kind="user", display_name="Alice", principal_id="alice")


@pytest.fixture
def bob(store):
    return store.create_principal(kind="user", display_name="Bob", principal_id="bob")


@pytest.fixture
def audit_writer(sqlite_backend):
    return AuditWriter(sqlite_backend)


@pytest.fixture
def manager(sqlite_backend, audit_writer):
    return ConnectorManager(sqlite_backend, audit_writer=audit_writer)


@pytest.fixture
def publisher(sqlite_backend, audit_writer):
    return MarketplacePublisher(sqlite_backend, audit_writer=audit_writer)


@pytest.fixture
def discovery(sqlite_backend, audit_writer):
    return MarketplaceDiscovery(sqlite_backend, audit_writer=audit_writer)


def _propose(manager, alice, org1, org2, *, name="c1"):
    return manager.propose(
        name=name, local_org_id=org1.id,
        remote_org_id=org2.id, remote_endpoint="https://example.com",
        created_by=alice.id,
    )


def _activate(manager, connector, alice):
    manager.submit_for_approval(connector.id, actor_id=alice.id)
    return manager.approve(connector.id, actor_id=alice.id)


# ---------------------------------------------------------------------------
# ConnectorManager — creator enforcement
# ---------------------------------------------------------------------------

class TestConnectorOwnerEnforcement:

    def test_non_creator_cannot_submit(self, manager, alice, bob, org1, org2):
        c = _propose(manager, alice, org1, org2)
        with pytest.raises(AccessDeniedError, match="not the creator"):
            manager.submit_for_approval(c.id, actor_id=bob.id)

    def test_non_creator_cannot_approve(self, manager, alice, bob, org1, org2):
        c = _propose(manager, alice, org1, org2)
        manager.submit_for_approval(c.id, actor_id=alice.id)
        with pytest.raises(AccessDeniedError, match="not the creator"):
            manager.approve(c.id, actor_id=bob.id)

    def test_non_creator_cannot_reject(self, manager, alice, bob, org1, org2):
        c = _propose(manager, alice, org1, org2)
        with pytest.raises(AccessDeniedError, match="not the creator"):
            manager.reject(c.id, actor_id=bob.id)

    def test_non_creator_cannot_suspend(self, manager, alice, bob, org1, org2):
        c = _activate(manager, _propose(manager, alice, org1, org2), alice)
        with pytest.raises(AccessDeniedError, match="not the creator"):
            manager.suspend(c.id, actor_id=bob.id)

    def test_non_creator_cannot_revoke(self, manager, alice, bob, org1, org2):
        c = _activate(manager, _propose(manager, alice, org1, org2), alice)
        with pytest.raises(AccessDeniedError, match="not the creator"):
            manager.revoke(c.id, actor_id=bob.id)

    def test_non_creator_cannot_add_policy(self, manager, alice, bob, org1, org2):
        c = _activate(manager, _propose(manager, alice, org1, org2), alice)
        with pytest.raises(AccessDeniedError, match="not the creator"):
            manager.add_policy(
                connector_id=c.id, policy_type=PolicyType.DENY_TYPES,
                config={"types": ["X"]}, created_by=bob.id,
            )


# ---------------------------------------------------------------------------
# ConnectorManager — audit + actor_id threading
# ---------------------------------------------------------------------------

class TestConnectorAudit:

    def test_add_policy_emits_audit(
        self, sqlite_backend, manager, alice, org1, org2,
    ):
        c = _activate(manager, _propose(manager, alice, org1, org2), alice)
        policy = manager.add_policy(
            connector_id=c.id, policy_type=PolicyType.ALLOW_TYPES,
            config={"types": ["Doc"]}, created_by=alice.id,
        )
        entries = AuditQuery(sqlite_backend).query(
            action=ActionType.CONNECTOR_POLICY_ADD,
        )
        assert any(e.target_id == policy.id for e in entries)

    def test_state_transitions_use_distinct_actions(
        self, sqlite_backend, manager, alice, org1, org2,
    ):
        c = _propose(manager, alice, org1, org2)
        manager.submit_for_approval(c.id, actor_id=alice.id)
        manager.approve(c.id, actor_id=alice.id)
        manager.suspend(c.id, actor_id=alice.id)
        manager.reactivate(c.id, actor_id=alice.id)
        manager.revoke(c.id, actor_id=alice.id)

        q = AuditQuery(sqlite_backend)
        # Each transition action category is now distinct (no more
        # collapsing suspend/reject under CONNECTOR_REVOKE).
        for action in (
            ActionType.CONNECTOR_PROPOSE,
            ActionType.CONNECTOR_SUBMIT,
            ActionType.CONNECTOR_APPROVE,
            ActionType.CONNECTOR_SUSPEND,
            ActionType.CONNECTOR_REVOKE,
        ):
            assert q.count(action=action, target_id=c.id) >= 1

    def test_sync_audit_actor_is_caller_not_connector(
        self, sqlite_backend, manager, alice, org1, org2,
    ):
        c = _activate(manager, _propose(manager, alice, org1, org2), alice)
        manager.sync_object(
            c.id, object_type="Document", actor_id=alice.id,
        )
        entries = AuditQuery(sqlite_backend).query(
            action=ActionType.CONNECTOR_SYNC,
        )
        assert any(e.actor_id == alice.id for e in entries)
        # And no entry should claim the connector ID as the actor.
        assert all(e.actor_id != c.id for e in entries)


# ---------------------------------------------------------------------------
# ConnectorManager — secret-leakage block
# ---------------------------------------------------------------------------

class TestSecretLeakageBlock:

    @pytest.mark.parametrize("forbidden", [
        "secret", "Secret", "SECRET",
        "credential", "Credentials",
        "api_key", "ApiKey",
        "private_key", "password",
        "secret_ref", "SecretRef",
    ])
    def test_secret_like_types_blocked(
        self, manager, alice, org1, org2, forbidden,
    ):
        c = _activate(manager, _propose(manager, alice, org1, org2), alice)
        with pytest.raises(ConnectorPolicyViolation, match="blocked"):
            manager.sync_object(
                c.id, object_type=forbidden, actor_id=alice.id,
            )


# ---------------------------------------------------------------------------
# MarketplacePublisher — publisher enforcement + validation
# ---------------------------------------------------------------------------

class TestMarketplaceOwnerEnforcement:

    def test_non_publisher_cannot_update_version(self, publisher, alice, bob):
        listing = publisher.publish(
            name="P", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN, version="1.0.0",
        )
        with pytest.raises(AccessDeniedError, match="not the publisher"):
            publisher.update_version(
                listing.id, new_version="2.0.0", actor_id=bob.id,
            )

    def test_non_publisher_cannot_deprecate(self, publisher, alice, bob):
        listing = publisher.publish(
            name="P", publisher_id=alice.id, listing_type=ListingType.PLUGIN,
        )
        with pytest.raises(AccessDeniedError, match="not the publisher"):
            publisher.deprecate(listing.id, actor_id=bob.id)

    def test_non_publisher_cannot_remove(self, publisher, alice, bob):
        listing = publisher.publish(
            name="P", publisher_id=alice.id, listing_type=ListingType.PLUGIN,
        )
        with pytest.raises(AccessDeniedError, match="not the publisher"):
            publisher.remove(listing.id, actor_id=bob.id)

    def test_non_publisher_cannot_change_visibility(self, publisher, alice, bob):
        listing = publisher.publish(
            name="P", publisher_id=alice.id, listing_type=ListingType.PLUGIN,
        )
        with pytest.raises(AccessDeniedError, match="not the publisher"):
            publisher.update_visibility(
                listing.id, visibility=Visibility.PRIVATE, actor_id=bob.id,
            )


class TestVersionValidation:

    def test_downgrade_rejected(self, publisher, alice):
        listing = publisher.publish(
            name="P", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN, version="2.0.0",
        )
        with pytest.raises(MarketplaceError, match="must be greater"):
            publisher.update_version(
                listing.id, new_version="1.0.0", actor_id=alice.id,
            )

    def test_same_version_rejected(self, publisher, alice):
        listing = publisher.publish(
            name="P", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN, version="1.2.3",
        )
        with pytest.raises(MarketplaceError, match="must be greater"):
            publisher.update_version(
                listing.id, new_version="1.2.3", actor_id=alice.id,
            )

    def test_invalid_semver_rejected(self, publisher, alice):
        listing = publisher.publish(
            name="P", publisher_id=alice.id, listing_type=ListingType.PLUGIN,
        )
        with pytest.raises(MarketplaceError, match="not a valid semver"):
            publisher.update_version(
                listing.id, new_version="banana", actor_id=alice.id,
            )

    def test_minor_bump_accepted(self, publisher, alice):
        listing = publisher.publish(
            name="P", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN, version="1.2.3",
        )
        updated = publisher.update_version(
            listing.id, new_version="1.3.0", actor_id=alice.id,
        )
        assert updated.version == "1.3.0"

    def test_cannot_version_archived(self, publisher, alice):
        listing = publisher.publish(
            name="P", publisher_id=alice.id, listing_type=ListingType.PLUGIN,
        )
        publisher.remove(listing.id, actor_id=alice.id)
        with pytest.raises(MarketplaceError, match="lifecycle"):
            publisher.update_version(
                listing.id, new_version="2.0.0", actor_id=alice.id,
            )


class TestMarketplaceAudit:

    def test_deprecate_emits_audit(self, sqlite_backend, publisher, alice):
        listing = publisher.publish(
            name="P", publisher_id=alice.id, listing_type=ListingType.PLUGIN,
        )
        publisher.deprecate(listing.id, actor_id=alice.id)
        entries = AuditQuery(sqlite_backend).query(
            action=ActionType.MARKETPLACE_DEPRECATE,
        )
        assert any(e.target_id == listing.id for e in entries)

    def test_remove_emits_audit(self, sqlite_backend, publisher, alice):
        listing = publisher.publish(
            name="P", publisher_id=alice.id, listing_type=ListingType.PLUGIN,
        )
        publisher.remove(listing.id, actor_id=alice.id)
        entries = AuditQuery(sqlite_backend).query(
            action=ActionType.MARKETPLACE_REMOVE,
        )
        assert any(e.target_id == listing.id for e in entries)

    def test_review_emits_audit(self, sqlite_backend, publisher, alice, bob):
        listing = publisher.publish(
            name="P", publisher_id=alice.id, listing_type=ListingType.PLUGIN,
        )
        review = publisher.add_review(
            listing_id=listing.id, reviewer_id=bob.id, rating=4,
        )
        entries = AuditQuery(sqlite_backend).query(
            action=ActionType.MARKETPLACE_REVIEW,
        )
        assert any(e.target_id == review.id for e in entries)

    def test_install_audit_targets_install_record(
        self, sqlite_backend, publisher, discovery, alice, bob,
    ):
        listing = publisher.publish(
            name="P", publisher_id=alice.id, listing_type=ListingType.PLUGIN,
        )
        install = discovery.install(listing.id, installer_id=bob.id)
        entries = AuditQuery(sqlite_backend).query(
            action=ActionType.MARKETPLACE_INSTALL,
        )
        # target_id should be the install record, not the listing.
        assert any(e.target_id == install.id for e in entries)

    def test_duplicate_review_raises_marketplace_error(
        self, publisher, alice, bob,
    ):
        listing = publisher.publish(
            name="P", publisher_id=alice.id, listing_type=ListingType.PLUGIN,
        )
        publisher.add_review(
            listing_id=listing.id, reviewer_id=bob.id, rating=4,
        )
        with pytest.raises(MarketplaceError, match="already reviewed"):
            publisher.add_review(
                listing_id=listing.id, reviewer_id=bob.id, rating=5,
            )


# ---------------------------------------------------------------------------
# MarketplaceDiscovery — visibility gate on install
# ---------------------------------------------------------------------------

class TestInstallVisibility:

    def test_private_install_blocked_for_others(
        self, publisher, discovery, alice, bob,
    ):
        listing = publisher.publish(
            name="Internal", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
            visibility=Visibility.PRIVATE,
        )
        with pytest.raises(AccessDeniedError, match="private"):
            discovery.install(listing.id, installer_id=bob.id)

    def test_private_install_allowed_for_publisher(
        self, publisher, discovery, alice,
    ):
        listing = publisher.publish(
            name="Internal", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
            visibility=Visibility.PRIVATE,
        )
        install = discovery.install(listing.id, installer_id=alice.id)
        assert install.installer_id == alice.id

    def test_unlisted_install_open(self, publisher, discovery, alice, bob):
        # Unlisted = unguessable but installable by anyone with the ID.
        listing = publisher.publish(
            name="Unlisted", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
            visibility=Visibility.UNLISTED,
        )
        install = discovery.install(listing.id, installer_id=bob.id)
        assert install.installer_id == bob.id


# ---------------------------------------------------------------------------
# FederationProtocol — sequence persistence + replay protection
# ---------------------------------------------------------------------------

class TestFederationSequencePersistence:

    def test_sequence_persists_across_instances(self, sqlite_backend):
        proto1 = FederationProtocol("k", backend=sqlite_backend)
        m1 = proto1.create_message(
            sender_org_id="us", receiver_org_id="them",
            connector_id="c1", message_type="sync", payload={},
        )
        m2 = proto1.create_message(
            sender_org_id="us", receiver_org_id="them",
            connector_id="c1", message_type="sync", payload={},
        )
        assert (m1.sequence, m2.sequence) == (1, 2)

        # Simulate process restart by constructing a new instance on
        # the same backend — the next sequence must be 3, not 1.
        proto2 = FederationProtocol("k", backend=sqlite_backend)
        m3 = proto2.create_message(
            sender_org_id="us", receiver_org_id="them",
            connector_id="c1", message_type="sync", payload={},
        )
        assert m3.sequence == 3

    def test_per_connector_sequence_isolation(self, sqlite_backend):
        proto = FederationProtocol("k", backend=sqlite_backend)
        m_a = proto.create_message(
            sender_org_id="us", receiver_org_id="them",
            connector_id="ca", message_type="sync", payload={},
        )
        m_b = proto.create_message(
            sender_org_id="us", receiver_org_id="them",
            connector_id="cb", message_type="sync", payload={},
        )
        # Each connector starts at 1.
        assert m_a.sequence == 1
        assert m_b.sequence == 1


class TestFederationReplayProtection:

    def test_accept_message_first_time_succeeds(self, sqlite_backend):
        sender = FederationProtocol("k", backend=sqlite_backend)
        msg = sender.create_message(
            sender_org_id="us", receiver_org_id="them",
            connector_id="c1", message_type="sync", payload={"hello": 1},
        )
        receiver = FederationProtocol("k", backend=sqlite_backend)
        # First accept is fine.
        receiver.accept_message(msg)

    def test_replay_rejected(self, sqlite_backend):
        sender = FederationProtocol("k", backend=sqlite_backend)
        msg = sender.create_message(
            sender_org_id="us", receiver_org_id="them",
            connector_id="c1", message_type="sync", payload={"hello": 1},
        )
        receiver = FederationProtocol("k", backend=sqlite_backend)
        receiver.accept_message(msg)
        with pytest.raises(FederationError, match="replay"):
            receiver.accept_message(msg)

    def test_replay_rejected_in_memory_fallback(self):
        # Without a backend, in-memory tracking still rejects replays.
        sender = FederationProtocol("k")
        msg = sender.create_message(
            sender_org_id="us", receiver_org_id="them",
            connector_id="c1", message_type="sync", payload={},
        )
        receiver = FederationProtocol("k")
        receiver.accept_message(msg)
        with pytest.raises(FederationError, match="replay"):
            receiver.accept_message(msg)

    def test_invalid_signature_rejected(self, sqlite_backend):
        sender = FederationProtocol("k", backend=sqlite_backend)
        msg = sender.create_message(
            sender_org_id="us", receiver_org_id="them",
            connector_id="c1", message_type="sync", payload={},
        )
        # Wrong shared key on the receiver.
        receiver = FederationProtocol("different-key", backend=sqlite_backend)
        with pytest.raises(FederationError, match="signature"):
            receiver.accept_message(msg)
