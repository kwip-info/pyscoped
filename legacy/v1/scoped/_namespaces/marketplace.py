"""Marketplace namespace — Layer 13 publishing + discovery + reviews.

Usage::

    import scoped

    with scoped.as_principal(alice):
        listing = scoped.marketplace.publish(
            name="My Plugin", listing_type=ListingType.PLUGIN,
        )
        scoped.marketplace.update_version(listing, new_version="1.1.0")
        scoped.marketplace.deprecate(listing)

    # Browse / install (any principal):
    with scoped.as_principal(bob):
        results = scoped.marketplace.browse(listing_type=ListingType.PLUGIN)
        install = scoped.marketplace.install(results[0])
        scoped.marketplace.review(results[0], rating=5,
                                   review_text="great")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from scoped._namespaces._base import _resolve_principal_id, _to_id

if TYPE_CHECKING:
    from scoped.connector.marketplace.models import (
        ListingType,
        MarketplaceInstall,
        MarketplaceListing,
        MarketplaceReview,
        Visibility,
    )


class MarketplaceNamespace:
    """Simplified API for Layer 13 marketplace.

    Wraps ``MarketplacePublisher`` (publishing/lifecycle/reviews) and
    ``MarketplaceDiscovery`` (browse/search/install) with context-aware
    defaults.
    """

    def __init__(self, services: Any) -> None:
        self._svc = services

    # -- Publishing --------------------------------------------------------

    def publish(
        self,
        name: str,
        *,
        listing_type: ListingType,
        publisher_id: str | None = None,
        description: str = "",
        version: str = "1.0.0",
        config_template: dict[str, Any] | None = None,
        visibility: Visibility | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MarketplaceListing:
        """Publish a new listing to the marketplace."""
        from scoped.connector.marketplace.models import Visibility as _V

        kwargs: dict[str, Any] = {
            "name": name,
            "publisher_id": _resolve_principal_id(publisher_id),
            "listing_type": listing_type,
            "description": description,
            "version": version,
            "config_template": config_template,
            "metadata": metadata,
        }
        kwargs["visibility"] = visibility if visibility is not None else _V.PUBLIC
        return self._svc.marketplace_publisher.publish(**kwargs)

    def update_version(
        self,
        listing: str | Any,
        *,
        new_version: str,
        config_template: dict[str, Any] | None = None,
        actor_id: str | None = None,
    ) -> MarketplaceListing:
        """Bump a listing's version (publisher-only, semver, ACTIVE only)."""
        return self._svc.marketplace_publisher.update_version(
            _to_id(listing),
            new_version=new_version,
            config_template=config_template,
            actor_id=_resolve_principal_id(actor_id),
        )

    def deprecate(
        self,
        listing: str | Any,
        *,
        actor_id: str | None = None,
    ) -> MarketplaceListing:
        """Mark a listing as deprecated (publisher-only, still discoverable)."""
        return self._svc.marketplace_publisher.deprecate(
            _to_id(listing), actor_id=_resolve_principal_id(actor_id),
        )

    def remove(
        self,
        listing: str | Any,
        *,
        actor_id: str | None = None,
    ) -> MarketplaceListing:
        """Archive a listing (publisher-only)."""
        return self._svc.marketplace_publisher.remove(
            _to_id(listing), actor_id=_resolve_principal_id(actor_id),
        )

    def update_visibility(
        self,
        listing: str | Any,
        *,
        visibility: Visibility,
        actor_id: str | None = None,
    ) -> MarketplaceListing:
        """Change a listing's visibility (publisher-only)."""
        return self._svc.marketplace_publisher.update_visibility(
            _to_id(listing),
            visibility=visibility,
            actor_id=_resolve_principal_id(actor_id),
        )

    def get(self, listing_id: str) -> MarketplaceListing | None:
        return self._svc.marketplace_publisher.get_listing(listing_id)

    # -- Reviews -----------------------------------------------------------

    def review(
        self,
        listing: str | Any,
        *,
        rating: int,
        review_text: str = "",
        reviewer_id: str | None = None,
    ) -> MarketplaceReview:
        """Add a review to a listing (one per reviewer per listing).

        Publishers cannot review their own listing.
        """
        return self._svc.marketplace_publisher.add_review(
            listing_id=_to_id(listing),
            reviewer_id=_resolve_principal_id(reviewer_id),
            rating=rating,
            review_text=review_text,
        )

    def reviews(
        self,
        listing: str | Any,
        *,
        limit: int = 100,
    ) -> list[MarketplaceReview]:
        return self._svc.marketplace_publisher.get_reviews(
            _to_id(listing), limit=limit,
        )

    def average_rating(self, listing: str | Any) -> float | None:
        return self._svc.marketplace_publisher.get_average_rating(
            _to_id(listing),
        )

    # -- Discovery ---------------------------------------------------------

    def browse(
        self,
        *,
        listing_type: ListingType | None = None,
        visibility: Visibility | None = None,
        active_only: bool = True,
        limit: int = 50,
    ) -> list[MarketplaceListing]:
        """Browse public listings (defaults to ``Visibility.PUBLIC``)."""
        from scoped.connector.marketplace.models import Visibility as _V

        return self._svc.marketplace_discovery.browse(
            listing_type=listing_type,
            visibility=visibility if visibility is not None else _V.PUBLIC,
            active_only=active_only,
            limit=limit,
        )

    def search(
        self,
        query: str,
        *,
        listing_type: ListingType | None = None,
        active_only: bool = True,
        limit: int = 50,
    ) -> list[MarketplaceListing]:
        """Search listings by name or description (excludes PRIVATE)."""
        return self._svc.marketplace_discovery.search(
            query, listing_type=listing_type,
            active_only=active_only, limit=limit,
        )

    def by_publisher(
        self,
        publisher: str | Any,
        *,
        limit: int = 100,
    ) -> list[MarketplaceListing]:
        return self._svc.marketplace_discovery.get_by_publisher(
            _to_id(publisher), limit=limit,
        )

    def install(
        self,
        listing: str | Any,
        *,
        installer_id: str | None = None,
        config: dict[str, Any] | None = None,
        result_ref: str | None = None,
        result_type: str | None = None,
    ) -> MarketplaceInstall:
        """Install a marketplace listing (PRIVATE = publisher-only)."""
        return self._svc.marketplace_discovery.install(
            _to_id(listing),
            installer_id=_resolve_principal_id(installer_id),
            config=config,
            result_ref=result_ref,
            result_type=result_type,
        )

    def installs(
        self,
        listing: str | Any,
        *,
        limit: int = 100,
    ) -> list[MarketplaceInstall]:
        return self._svc.marketplace_discovery.get_installs(
            _to_id(listing), limit=limit,
        )

    def my_installs(
        self,
        installer: str | Any | None = None,
        *,
        limit: int = 100,
    ) -> list[MarketplaceInstall]:
        installer_id = (
            _to_id(installer) if installer is not None
            else _resolve_principal_id(None)
        )
        return self._svc.marketplace_discovery.get_installs_by_user(
            installer_id, limit=limit,
        )
