"""Marketplace publishing — create, version, deprecate, and remove listings."""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from scoped.connector.marketplace.models import (
    ListingType,
    MarketplaceListing,
    MarketplaceReview,
    Visibility,
    listing_from_row,
    review_from_row,
)
from scoped.exceptions import MarketplaceError
from scoped.storage._query import compile_for
from scoped.storage._schema import marketplace_listings, marketplace_reviews
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc
from scoped._stability import preview


@preview()
class MarketplacePublisher:
    """Publish, version, deprecate, and remove marketplace listings."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer

    def publish(
        self,
        *,
        name: str,
        publisher_id: str,
        listing_type: ListingType,
        description: str = "",
        version: str = "1.0.0",
        config_template: dict[str, Any] | None = None,
        visibility: Visibility = Visibility.PUBLIC,
        metadata: dict[str, Any] | None = None,
    ) -> MarketplaceListing:
        """Publish a new listing to the marketplace."""
        ts = now_utc()
        lid = generate_id()
        cfg = config_template or {}
        meta = metadata or {}

        listing = MarketplaceListing(
            id=lid,
            name=name,
            description=description,
            publisher_id=publisher_id,
            listing_type=listing_type,
            version=version,
            config_template=cfg,
            visibility=visibility,
            published_at=ts,
            lifecycle=Lifecycle.ACTIVE,
            metadata=meta,
        )

        stmt = sa.insert(marketplace_listings).values(
            id=lid,
            name=name,
            description=description,
            publisher_id=publisher_id,
            listing_type=listing_type.value,
            version=version,
            config_template=json.dumps(cfg),
            visibility=visibility.value,
            published_at=ts.isoformat(),
            updated_at=None,
            lifecycle="ACTIVE",
            download_count=0,
            metadata_json=json.dumps(meta),
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit is not None:
            self._audit.record(
                actor_id=publisher_id,
                action=ActionType.MARKETPLACE_PUBLISH,
                target_type="marketplace_listing",
                target_id=lid,
                after_state=listing.snapshot(),
            )

        return listing

    def get_listing(self, listing_id: str) -> MarketplaceListing | None:
        stmt = sa.select(marketplace_listings).where(
            marketplace_listings.c.id == listing_id,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return listing_from_row(row) if row else None

    def get_listing_or_raise(self, listing_id: str) -> MarketplaceListing:
        listing = self.get_listing(listing_id)
        if listing is None:
            from scoped.exceptions import ListingNotFoundError
            raise ListingNotFoundError(
                f"Listing {listing_id} not found",
                context={"listing_id": listing_id},
            )
        return listing

    def update_version(
        self,
        listing_id: str,
        *,
        new_version: str,
        config_template: dict[str, Any] | None = None,
        actor_id: str,
    ) -> MarketplaceListing:
        """Update a listing to a new version."""
        listing = self.get_listing_or_raise(listing_id)
        ts = now_utc()

        values: dict[str, Any] = {
            "version": new_version,
            "updated_at": ts.isoformat(),
        }

        if config_template is not None:
            values["config_template"] = json.dumps(config_template)

        stmt = sa.update(marketplace_listings).where(
            marketplace_listings.c.id == listing_id,
        ).values(**values)
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        listing.version = new_version
        listing.updated_at = ts
        if config_template is not None:
            listing.config_template = config_template

        return listing

    def deprecate(self, listing_id: str, *, actor_id: str) -> MarketplaceListing:
        """Deprecate a listing (still visible but flagged)."""
        listing = self.get_listing_or_raise(listing_id)
        ts = now_utc()
        stmt = sa.update(marketplace_listings).where(
            marketplace_listings.c.id == listing_id,
        ).values(lifecycle="DEPRECATED", updated_at=ts.isoformat())
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
        listing.lifecycle = Lifecycle.DEPRECATED
        listing.updated_at = ts
        return listing

    def remove(self, listing_id: str, *, actor_id: str) -> MarketplaceListing:
        """Remove a listing from the marketplace."""
        listing = self.get_listing_or_raise(listing_id)
        ts = now_utc()
        stmt = sa.update(marketplace_listings).where(
            marketplace_listings.c.id == listing_id,
        ).values(lifecycle="ARCHIVED", updated_at=ts.isoformat())
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
        listing.lifecycle = Lifecycle.ARCHIVED
        listing.updated_at = ts
        return listing

    def update_visibility(
        self,
        listing_id: str,
        *,
        visibility: Visibility,
        actor_id: str,
    ) -> MarketplaceListing:
        """Change a listing's visibility."""
        listing = self.get_listing_or_raise(listing_id)
        ts = now_utc()
        stmt = sa.update(marketplace_listings).where(
            marketplace_listings.c.id == listing_id,
        ).values(visibility=visibility.value, updated_at=ts.isoformat())
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
        listing.visibility = visibility
        listing.updated_at = ts
        return listing

    # -- Reviews -----------------------------------------------------------

    def add_review(
        self,
        *,
        listing_id: str,
        reviewer_id: str,
        rating: int,
        review_text: str = "",
    ) -> MarketplaceReview:
        """Add a review to a listing. One per reviewer per listing."""
        self.get_listing_or_raise(listing_id)
        if not 1 <= rating <= 5:
            raise MarketplaceError(
                f"Rating must be 1-5, got {rating}",
                context={"rating": rating},
            )

        ts = now_utc()
        rid = generate_id()

        review = MarketplaceReview(
            id=rid,
            listing_id=listing_id,
            reviewer_id=reviewer_id,
            rating=rating,
            review_text=review_text,
            reviewed_at=ts,
        )

        stmt = sa.insert(marketplace_reviews).values(
            id=rid,
            listing_id=listing_id,
            reviewer_id=reviewer_id,
            rating=rating,
            review_text=review_text,
            reviewed_at=ts.isoformat(),
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        return review

    def get_reviews(
        self,
        listing_id: str,
        *,
        limit: int = 100,
    ) -> list[MarketplaceReview]:
        stmt = sa.select(marketplace_reviews).where(
            marketplace_reviews.c.listing_id == listing_id,
        ).order_by(marketplace_reviews.c.reviewed_at.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [review_from_row(r) for r in rows]

    def get_average_rating(self, listing_id: str) -> float | None:
        """Get average rating for a listing. Returns None if no reviews."""
        stmt = sa.select(
            sa.func.avg(marketplace_reviews.c.rating).label("avg_rating"),
            sa.func.count().label("cnt"),
        ).where(marketplace_reviews.c.listing_id == listing_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None or row["cnt"] == 0:
            return None
        return row["avg_rating"]
