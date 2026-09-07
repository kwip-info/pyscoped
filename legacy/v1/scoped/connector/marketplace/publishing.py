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
from scoped.exceptions import AccessDeniedError, MarketplaceError
from scoped.storage._query import compile_for
from scoped.storage._schema import marketplace_listings, marketplace_reviews
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc
from scoped._stability import stable


@stable(since="1.8.0")
class MarketplacePublisher:
    """Publish, version, deprecate, and remove marketplace listings."""

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
    ) -> None:
        """Layer 5 rule gate. Default-permit when no rules are bound."""
        if self._rule_engine is None:
            return
        result = self._rule_engine.evaluate(
            action=action,
            principal_id=principal_id,
            object_type="marketplace_listing",
            object_id=object_id,
        )
        if not result.allowed and (result.deny_rules or result.matching_rules):
            deny_names = [r.name for r in result.deny_rules]
            raise AccessDeniedError(
                f"{action} denied by rule(s): {deny_names}",
                context={
                    "action": action,
                    "principal_id": principal_id,
                    "object_id": object_id,
                    "deny_rules": deny_names,
                },
            )

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
        self._check_rule(
            action="marketplace_publish",
            principal_id=publisher_id,
        )
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

    def _require_publisher(
        self,
        listing: MarketplaceListing,
        actor_id: str,
    ) -> None:
        """Raise AccessDeniedError if the actor is not the listing publisher."""
        if listing.publisher_id != actor_id:
            raise AccessDeniedError(
                f"Principal '{actor_id}' is not the publisher of listing "
                f"'{listing.id}'",
                context={
                    "listing_id": listing.id,
                    "actor_id": actor_id,
                    "publisher_id": listing.publisher_id,
                },
            )

    @staticmethod
    def _parse_semver(version: str) -> tuple[int, int, int]:
        """Parse a semver-like ``X.Y.Z`` string into a comparable tuple.

        Tolerates pre-release / build metadata suffixes (``1.2.3-rc1``)
        by truncating at the first ``-`` or ``+``. Raises
        ``MarketplaceError`` if the version isn't parseable.
        """
        if not version:
            raise MarketplaceError(
                "Version must be a non-empty string",
                context={"version": version},
            )
        core = version.split("-", 1)[0].split("+", 1)[0]
        parts = core.split(".")
        if len(parts) > 3:
            raise MarketplaceError(
                f"Version '{version}' is not a valid semver (X.Y.Z)",
                context={"version": version},
            )
        try:
            nums = [int(p) for p in parts]
        except ValueError as exc:
            raise MarketplaceError(
                f"Version '{version}' is not a valid semver (X.Y.Z)",
                context={"version": version},
            ) from exc
        while len(nums) < 3:
            nums.append(0)
        return (nums[0], nums[1], nums[2])

    def update_version(
        self,
        listing_id: str,
        *,
        new_version: str,
        config_template: dict[str, Any] | None = None,
        actor_id: str,
    ) -> MarketplaceListing:
        """Update a listing to a new version (publisher-only).

        The new version must be a valid semver and strictly higher than
        the current version. Only ``ACTIVE`` listings may be version-bumped;
        deprecated or archived listings raise ``MarketplaceError``.
        """
        listing = self.get_listing_or_raise(listing_id)
        self._require_publisher(listing, actor_id)

        if listing.lifecycle != Lifecycle.ACTIVE:
            raise MarketplaceError(
                f"Cannot version-bump listing in lifecycle "
                f"{listing.lifecycle.name}",
                context={
                    "listing_id": listing_id,
                    "lifecycle": listing.lifecycle.name,
                },
            )

        new_parts = self._parse_semver(new_version)
        cur_parts = self._parse_semver(listing.version)
        if new_parts <= cur_parts:
            raise MarketplaceError(
                f"New version '{new_version}' must be greater than "
                f"current version '{listing.version}'",
                context={
                    "listing_id": listing_id,
                    "current_version": listing.version,
                    "new_version": new_version,
                },
            )

        ts = now_utc()
        old_version = listing.version

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

        if self._audit is not None:
            self._audit.record(
                actor_id=actor_id,
                action=ActionType.MARKETPLACE_VERSION_UPDATE,
                target_type="marketplace_listing",
                target_id=listing_id,
                metadata={
                    "old_version": old_version,
                    "new_version": new_version,
                },
                after_state=listing.snapshot(),
            )

        return listing

    def deprecate(self, listing_id: str, *, actor_id: str) -> MarketplaceListing:
        """Deprecate a listing (publisher-only, still visible but flagged)."""
        listing = self.get_listing_or_raise(listing_id)
        self._require_publisher(listing, actor_id)
        ts = now_utc()
        stmt = sa.update(marketplace_listings).where(
            marketplace_listings.c.id == listing_id,
        ).values(lifecycle="DEPRECATED", updated_at=ts.isoformat())
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
        listing.lifecycle = Lifecycle.DEPRECATED
        listing.updated_at = ts

        if self._audit is not None:
            self._audit.record(
                actor_id=actor_id,
                action=ActionType.MARKETPLACE_DEPRECATE,
                target_type="marketplace_listing",
                target_id=listing_id,
                after_state=listing.snapshot(),
            )

        return listing

    def remove(self, listing_id: str, *, actor_id: str) -> MarketplaceListing:
        """Remove a listing from the marketplace (publisher-only)."""
        listing = self.get_listing_or_raise(listing_id)
        self._require_publisher(listing, actor_id)
        ts = now_utc()
        stmt = sa.update(marketplace_listings).where(
            marketplace_listings.c.id == listing_id,
        ).values(lifecycle="ARCHIVED", updated_at=ts.isoformat())
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
        listing.lifecycle = Lifecycle.ARCHIVED
        listing.updated_at = ts

        if self._audit is not None:
            self._audit.record(
                actor_id=actor_id,
                action=ActionType.MARKETPLACE_REMOVE,
                target_type="marketplace_listing",
                target_id=listing_id,
                after_state=listing.snapshot(),
            )

        return listing

    def update_visibility(
        self,
        listing_id: str,
        *,
        visibility: Visibility,
        actor_id: str,
    ) -> MarketplaceListing:
        """Change a listing's visibility (publisher-only)."""
        listing = self.get_listing_or_raise(listing_id)
        self._require_publisher(listing, actor_id)
        ts = now_utc()
        old_visibility = listing.visibility
        stmt = sa.update(marketplace_listings).where(
            marketplace_listings.c.id == listing_id,
        ).values(visibility=visibility.value, updated_at=ts.isoformat())
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
        listing.visibility = visibility
        listing.updated_at = ts

        if self._audit is not None:
            self._audit.record(
                actor_id=actor_id,
                action=ActionType.MARKETPLACE_VISIBILITY_CHANGE,
                target_type="marketplace_listing",
                target_id=listing_id,
                metadata={
                    "old_visibility": old_visibility.value,
                    "new_visibility": visibility.value,
                },
                after_state=listing.snapshot(),
            )

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
        """Add a review to a listing. One per reviewer per listing.

        Publishers may not review their own listing. Duplicate reviews
        (same reviewer + listing) raise ``MarketplaceError``.
        """
        listing = self.get_listing_or_raise(listing_id)
        if listing.publisher_id == reviewer_id:
            raise MarketplaceError(
                "Publishers cannot review their own listing",
                context={
                    "listing_id": listing_id,
                    "reviewer_id": reviewer_id,
                },
            )
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
        try:
            self._backend.execute(sql, params)
        except Exception as exc:
            # Translate UNIQUE-constraint violation on (listing_id,
            # reviewer_id) into a clean MarketplaceError.
            msg = str(exc).lower()
            if "unique" in msg or "integrity" in msg or "duplicate" in msg:
                raise MarketplaceError(
                    f"Reviewer '{reviewer_id}' has already reviewed "
                    f"listing '{listing_id}'",
                    context={
                        "listing_id": listing_id,
                        "reviewer_id": reviewer_id,
                    },
                ) from exc
            raise

        if self._audit is not None:
            self._audit.record(
                actor_id=reviewer_id,
                action=ActionType.MARKETPLACE_REVIEW,
                target_type="marketplace_review",
                target_id=rid,
                metadata={
                    "listing_id": listing_id,
                    "rating": rating,
                },
            )

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
