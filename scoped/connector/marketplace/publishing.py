"""Marketplace publishing — create, version, deprecate, and remove listings."""

from __future__ import annotations

import json
from typing import Any

from scoped.connector.marketplace.models import (
    ListingType,
    MarketplaceListing,
    MarketplaceReview,
    Visibility,
    listing_from_row,
    review_from_row,
)
from scoped.exceptions import MarketplaceError
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc


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

        self._backend.execute(
            """INSERT INTO marketplace_listings
               (id, name, description, publisher_id, listing_type, version,
                config_template, visibility, published_at, updated_at,
                lifecycle, download_count, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (lid, name, description, publisher_id, listing_type.value, version,
             json.dumps(cfg), visibility.value, ts.isoformat(), None,
             "ACTIVE", 0, json.dumps(meta)),
        )

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
        row = self._backend.fetch_one(
            "SELECT * FROM marketplace_listings WHERE id = ?", (listing_id,),
        )
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

        updates = ["version = ?", "updated_at = ?"]
        params: list[Any] = [new_version, ts.isoformat()]

        if config_template is not None:
            updates.append("config_template = ?")
            params.append(json.dumps(config_template))

        params.append(listing_id)
        self._backend.execute(
            f"UPDATE marketplace_listings SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )

        listing.version = new_version
        listing.updated_at = ts
        if config_template is not None:
            listing.config_template = config_template

        return listing

    def deprecate(self, listing_id: str, *, actor_id: str) -> MarketplaceListing:
        """Deprecate a listing (still visible but flagged)."""
        listing = self.get_listing_or_raise(listing_id)
        ts = now_utc()
        self._backend.execute(
            "UPDATE marketplace_listings SET lifecycle = 'DEPRECATED', updated_at = ? WHERE id = ?",
            (ts.isoformat(), listing_id),
        )
        listing.lifecycle = Lifecycle.DEPRECATED
        listing.updated_at = ts
        return listing

    def remove(self, listing_id: str, *, actor_id: str) -> MarketplaceListing:
        """Remove a listing from the marketplace."""
        listing = self.get_listing_or_raise(listing_id)
        ts = now_utc()
        self._backend.execute(
            "UPDATE marketplace_listings SET lifecycle = 'ARCHIVED', updated_at = ? WHERE id = ?",
            (ts.isoformat(), listing_id),
        )
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
        self._backend.execute(
            "UPDATE marketplace_listings SET visibility = ?, updated_at = ? WHERE id = ?",
            (visibility.value, ts.isoformat(), listing_id),
        )
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

        self._backend.execute(
            """INSERT INTO marketplace_reviews
               (id, listing_id, reviewer_id, rating, review_text, reviewed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (rid, listing_id, reviewer_id, rating, review_text, ts.isoformat()),
        )

        return review

    def get_reviews(
        self,
        listing_id: str,
        *,
        limit: int = 100,
    ) -> list[MarketplaceReview]:
        rows = self._backend.fetch_all(
            "SELECT * FROM marketplace_reviews WHERE listing_id = ? ORDER BY reviewed_at DESC LIMIT ?",
            (listing_id, limit),
        )
        return [review_from_row(r) for r in rows]

    def get_average_rating(self, listing_id: str) -> float | None:
        """Get average rating for a listing. Returns None if no reviews."""
        row = self._backend.fetch_one(
            "SELECT AVG(rating) as avg_rating, COUNT(*) as cnt FROM marketplace_reviews WHERE listing_id = ?",
            (listing_id,),
        )
        if row is None or row["cnt"] == 0:
            return None
        return row["avg_rating"]
