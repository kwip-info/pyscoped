"""Tests for marketplace — publishing, discovery, reviews, installs."""

import pytest

from scoped.connector.marketplace.discovery import MarketplaceDiscovery
from scoped.connector.marketplace.models import ListingType, Visibility
from scoped.connector.marketplace.publishing import MarketplacePublisher
from scoped.exceptions import ListingNotFoundError, MarketplaceError
from scoped.identity.principal import PrincipalStore
from scoped.types import Lifecycle


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    bob = store.create_principal(kind="user", display_name="Bob", principal_id="bob")
    return alice, bob


@pytest.fixture
def publisher(sqlite_backend):
    return MarketplacePublisher(sqlite_backend)


@pytest.fixture
def discovery(sqlite_backend):
    return MarketplaceDiscovery(sqlite_backend)


class TestPublish:

    def test_basic_publish(self, publisher, principals):
        alice, _ = principals
        listing = publisher.publish(
            name="Auth Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
            description="Authentication plugin",
        )
        assert listing.name == "Auth Plugin"
        assert listing.listing_type == ListingType.PLUGIN
        assert listing.is_active
        assert listing.is_public

    def test_publish_connector_template(self, publisher, principals):
        alice, _ = principals
        listing = publisher.publish(
            name="Data Sync", publisher_id=alice.id,
            listing_type=ListingType.CONNECTOR_TEMPLATE,
            config_template={"sync_interval": 3600},
        )
        assert listing.listing_type == ListingType.CONNECTOR_TEMPLATE
        assert listing.config_template["sync_interval"] == 3600

    def test_publish_unlisted(self, publisher, principals):
        alice, _ = principals
        listing = publisher.publish(
            name="Private-ish", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
            visibility=Visibility.UNLISTED,
        )
        assert listing.visibility == Visibility.UNLISTED
        assert not listing.is_public

    def test_publish_with_version(self, publisher, principals):
        alice, _ = principals
        listing = publisher.publish(
            name="Versioned", publisher_id=alice.id,
            listing_type=ListingType.INTEGRATION,
            version="3.0.0",
        )
        assert listing.version == "3.0.0"


class TestGetListing:

    def test_get_existing(self, publisher, principals):
        alice, _ = principals
        created = publisher.publish(
            name="Test", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        fetched = publisher.get_listing(created.id)
        assert fetched is not None
        assert fetched.id == created.id

    def test_get_nonexistent(self, publisher):
        assert publisher.get_listing("nope") is None

    def test_get_or_raise(self, publisher):
        with pytest.raises(ListingNotFoundError, match="not found"):
            publisher.get_listing_or_raise("nope")


class TestUpdateVersion:

    def test_update_version(self, publisher, principals):
        alice, _ = principals
        listing = publisher.publish(
            name="Test", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN, version="1.0.0",
        )
        updated = publisher.update_version(
            listing.id, new_version="2.0.0", actor_id=alice.id,
        )
        assert updated.version == "2.0.0"
        assert updated.updated_at is not None

    def test_update_with_config(self, publisher, principals):
        alice, _ = principals
        listing = publisher.publish(
            name="Test", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
            config_template={"old": True},
        )
        updated = publisher.update_version(
            listing.id, new_version="2.0.0",
            config_template={"new": True},
            actor_id=alice.id,
        )
        assert updated.config_template == {"new": True}


class TestDeprecateAndRemove:

    def test_deprecate(self, publisher, principals):
        alice, _ = principals
        listing = publisher.publish(
            name="Old Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        deprecated = publisher.deprecate(listing.id, actor_id=alice.id)
        assert deprecated.lifecycle == Lifecycle.DEPRECATED

    def test_remove(self, publisher, principals):
        alice, _ = principals
        listing = publisher.publish(
            name="Bad Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        removed = publisher.remove(listing.id, actor_id=alice.id)
        assert removed.lifecycle == Lifecycle.ARCHIVED

    def test_update_visibility(self, publisher, principals):
        alice, _ = principals
        listing = publisher.publish(
            name="Public", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        updated = publisher.update_visibility(
            listing.id, visibility=Visibility.PRIVATE, actor_id=alice.id,
        )
        assert updated.visibility == Visibility.PRIVATE


class TestReviews:

    def test_add_review(self, publisher, principals):
        alice, bob = principals
        listing = publisher.publish(
            name="Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        review = publisher.add_review(
            listing_id=listing.id, reviewer_id=bob.id,
            rating=5, review_text="Excellent!",
        )
        assert review.rating == 5
        assert review.review_text == "Excellent!"

    def test_invalid_rating(self, publisher, principals):
        alice, bob = principals
        listing = publisher.publish(
            name="Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        with pytest.raises(MarketplaceError, match="Rating must be 1-5"):
            publisher.add_review(
                listing_id=listing.id, reviewer_id=bob.id, rating=6,
            )

    def test_invalid_rating_zero(self, publisher, principals):
        alice, bob = principals
        listing = publisher.publish(
            name="Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        with pytest.raises(MarketplaceError, match="Rating must be 1-5"):
            publisher.add_review(
                listing_id=listing.id, reviewer_id=bob.id, rating=0,
            )

    def test_one_review_per_user(self, publisher, principals):
        alice, bob = principals
        listing = publisher.publish(
            name="Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        publisher.add_review(
            listing_id=listing.id, reviewer_id=bob.id, rating=4,
        )
        with pytest.raises(Exception):  # UNIQUE constraint
            publisher.add_review(
                listing_id=listing.id, reviewer_id=bob.id, rating=3,
            )

    def test_get_reviews(self, publisher, principals):
        alice, bob = principals
        listing = publisher.publish(
            name="Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        publisher.add_review(
            listing_id=listing.id, reviewer_id=alice.id, rating=3,
        )
        publisher.add_review(
            listing_id=listing.id, reviewer_id=bob.id, rating=5,
        )
        reviews = publisher.get_reviews(listing.id)
        assert len(reviews) == 2

    def test_average_rating(self, publisher, principals):
        alice, bob = principals
        listing = publisher.publish(
            name="Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        publisher.add_review(
            listing_id=listing.id, reviewer_id=alice.id, rating=3,
        )
        publisher.add_review(
            listing_id=listing.id, reviewer_id=bob.id, rating=5,
        )
        avg = publisher.get_average_rating(listing.id)
        assert avg == 4.0

    def test_average_rating_no_reviews(self, publisher, principals):
        alice, _ = principals
        listing = publisher.publish(
            name="Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        assert publisher.get_average_rating(listing.id) is None


class TestBrowse:

    def test_browse_public(self, publisher, discovery, principals):
        alice, _ = principals
        publisher.publish(
            name="Public Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        publisher.publish(
            name="Private Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN, visibility=Visibility.PRIVATE,
        )
        results = discovery.browse()
        assert len(results) == 1
        assert results[0].name == "Public Plugin"

    def test_browse_by_type(self, publisher, discovery, principals):
        alice, _ = principals
        publisher.publish(
            name="Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        publisher.publish(
            name="Integration", publisher_id=alice.id,
            listing_type=ListingType.INTEGRATION,
        )
        results = discovery.browse(listing_type=ListingType.PLUGIN)
        assert len(results) == 1
        assert results[0].name == "Plugin"

    def test_browse_excludes_archived(self, publisher, discovery, principals):
        alice, _ = principals
        listing = publisher.publish(
            name="Removed", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        publisher.remove(listing.id, actor_id=alice.id)
        results = discovery.browse()
        assert len(results) == 0


class TestSearch:

    def test_search_by_name(self, publisher, discovery, principals):
        alice, _ = principals
        publisher.publish(
            name="Auth Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN, description="Authentication",
        )
        publisher.publish(
            name="Logging Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN, description="Logging",
        )
        results = discovery.search("Auth")
        assert len(results) == 1
        assert results[0].name == "Auth Plugin"

    def test_search_by_description(self, publisher, discovery, principals):
        alice, _ = principals
        publisher.publish(
            name="My Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
            description="Handles OAuth2 authentication",
        )
        results = discovery.search("OAuth2")
        assert len(results) == 1

    def test_search_excludes_private(self, publisher, discovery, principals):
        alice, _ = principals
        publisher.publish(
            name="Secret Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
            visibility=Visibility.PRIVATE,
        )
        results = discovery.search("Secret")
        assert len(results) == 0

    def test_search_by_type(self, publisher, discovery, principals):
        alice, _ = principals
        publisher.publish(
            name="Auth Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        publisher.publish(
            name="Auth Integration", publisher_id=alice.id,
            listing_type=ListingType.INTEGRATION,
        )
        results = discovery.search("Auth", listing_type=ListingType.PLUGIN)
        assert len(results) == 1
        assert results[0].listing_type == ListingType.PLUGIN

    def test_get_by_publisher(self, publisher, discovery, principals):
        alice, bob = principals
        publisher.publish(
            name="Alice Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        publisher.publish(
            name="Bob Plugin", publisher_id=bob.id,
            listing_type=ListingType.PLUGIN,
        )
        results = discovery.get_by_publisher(alice.id)
        assert len(results) == 1
        assert results[0].name == "Alice Plugin"


class TestInstall:

    def test_install_listing(self, publisher, discovery, principals):
        alice, bob = principals
        listing = publisher.publish(
            name="Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        install = discovery.install(
            listing.id, installer_id=bob.id,
            config={"custom": "setting"},
        )
        assert install.listing_id == listing.id
        assert install.installer_id == bob.id
        assert install.version == listing.version
        assert install.config == {"custom": "setting"}

    def test_install_increments_download_count(self, publisher, discovery, principals):
        alice, bob = principals
        listing = publisher.publish(
            name="Popular", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        discovery.install(listing.id, installer_id=bob.id)
        discovery.install(listing.id, installer_id=alice.id)

        updated = publisher.get_listing(listing.id)
        assert updated.download_count == 2

    def test_install_with_result_ref(self, publisher, discovery, principals):
        alice, bob = principals
        listing = publisher.publish(
            name="Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        install = discovery.install(
            listing.id, installer_id=bob.id,
            result_ref="plugin-instance-123",
            result_type="plugin",
        )
        assert install.result_ref == "plugin-instance-123"
        assert install.result_type == "plugin"

    def test_install_nonexistent(self, discovery, principals):
        _, bob = principals
        with pytest.raises(ListingNotFoundError):
            discovery.install("nonexistent", installer_id=bob.id)

    def test_install_archived_listing(self, publisher, discovery, principals):
        alice, bob = principals
        listing = publisher.publish(
            name="Removed", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        publisher.remove(listing.id, actor_id=alice.id)
        with pytest.raises(MarketplaceError, match="not active"):
            discovery.install(listing.id, installer_id=bob.id)

    def test_get_installs_for_listing(self, publisher, discovery, principals):
        alice, bob = principals
        listing = publisher.publish(
            name="Plugin", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        discovery.install(listing.id, installer_id=bob.id)
        discovery.install(listing.id, installer_id=alice.id)
        installs = discovery.get_installs(listing.id)
        assert len(installs) == 2

    def test_get_installs_by_user(self, publisher, discovery, principals):
        alice, bob = principals
        l1 = publisher.publish(
            name="P1", publisher_id=alice.id,
            listing_type=ListingType.PLUGIN,
        )
        l2 = publisher.publish(
            name="P2", publisher_id=alice.id,
            listing_type=ListingType.INTEGRATION,
        )
        discovery.install(l1.id, installer_id=bob.id)
        discovery.install(l2.id, installer_id=bob.id)
        installs = discovery.get_installs_by_user(bob.id)
        assert len(installs) == 2
