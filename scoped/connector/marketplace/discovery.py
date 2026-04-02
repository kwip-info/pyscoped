"""Marketplace discovery — search, filter, browse, and install listings."""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from scoped.connector.marketplace.models import (
    ListingType,
    MarketplaceInstall,
    MarketplaceListing,
    Visibility,
    install_from_row,
    listing_from_row,
)
from scoped.exceptions import ListingNotFoundError, MarketplaceError
from scoped.storage._query import compile_for
from scoped.storage._schema import marketplace_installs, marketplace_listings
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, generate_id, now_utc
from scoped._stability import preview


@preview()
class MarketplaceDiscovery:
    """Search, filter, browse, and install marketplace listings."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer

    def browse(
        self,
        *,
        listing_type: ListingType | None = None,
        visibility: Visibility = Visibility.PUBLIC,
        active_only: bool = True,
        limit: int = 50,
    ) -> list[MarketplaceListing]:
        """Browse marketplace listings."""
        stmt = sa.select(marketplace_listings).where(
            marketplace_listings.c.visibility == visibility.value,
        )
        if listing_type is not None:
            stmt = stmt.where(marketplace_listings.c.listing_type == listing_type.value)
        if active_only:
            stmt = stmt.where(marketplace_listings.c.lifecycle == "ACTIVE")
        stmt = stmt.order_by(
            marketplace_listings.c.download_count.desc(),
            marketplace_listings.c.published_at.desc(),
        ).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [listing_from_row(r) for r in rows]

    def search(
        self,
        query: str,
        *,
        listing_type: ListingType | None = None,
        active_only: bool = True,
        limit: int = 50,
    ) -> list[MarketplaceListing]:
        """Search listings by name or description."""
        pattern = f"%{query}%"
        stmt = sa.select(marketplace_listings).where(
            (marketplace_listings.c.name.like(pattern))
            | (marketplace_listings.c.description.like(pattern)),
        )
        if listing_type is not None:
            stmt = stmt.where(marketplace_listings.c.listing_type == listing_type.value)
        if active_only:
            stmt = stmt.where(marketplace_listings.c.lifecycle == "ACTIVE")
        # Public and unlisted are searchable; private are not
        stmt = stmt.where(marketplace_listings.c.visibility != "private")
        stmt = stmt.order_by(marketplace_listings.c.download_count.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [listing_from_row(r) for r in rows]

    def get_by_publisher(
        self,
        publisher_id: str,
        *,
        limit: int = 100,
    ) -> list[MarketplaceListing]:
        """Get all listings by a specific publisher."""
        stmt = sa.select(marketplace_listings).where(
            marketplace_listings.c.publisher_id == publisher_id,
        ).order_by(marketplace_listings.c.published_at.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [listing_from_row(r) for r in rows]

    def install(
        self,
        listing_id: str,
        *,
        installer_id: str,
        config: dict[str, Any] | None = None,
        result_ref: str | None = None,
        result_type: str | None = None,
    ) -> MarketplaceInstall:
        """Install a marketplace listing, creating a private instance.

        The listing is a blueprint — the install creates a private copy.
        Increments the listing's download count.
        """
        # Verify listing exists and is active
        stmt = sa.select(marketplace_listings).where(
            marketplace_listings.c.id == listing_id,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            raise ListingNotFoundError(
                f"Listing {listing_id} not found",
                context={"listing_id": listing_id},
            )
        listing = listing_from_row(row)
        if not listing.is_active:
            raise MarketplaceError(
                f"Listing {listing_id} is not active (lifecycle: {listing.lifecycle.name})",
                context={"listing_id": listing_id},
            )

        ts = now_utc()
        iid = generate_id()
        cfg = config or {}

        install_record = MarketplaceInstall(
            id=iid,
            listing_id=listing_id,
            installer_id=installer_id,
            installed_at=ts,
            version=listing.version,
            config=cfg,
            result_ref=result_ref,
            result_type=result_type,
        )

        stmt = sa.insert(marketplace_installs).values(
            id=iid,
            listing_id=listing_id,
            installer_id=installer_id,
            installed_at=ts.isoformat(),
            version=listing.version,
            config_json=json.dumps(cfg),
            result_ref=result_ref,
            result_type=result_type,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        # Increment download count
        stmt = sa.update(marketplace_listings).where(
            marketplace_listings.c.id == listing_id,
        ).values(
            download_count=marketplace_listings.c.download_count + 1,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit is not None:
            self._audit.record(
                actor_id=installer_id,
                action=ActionType.MARKETPLACE_INSTALL,
                target_type="marketplace_listing",
                target_id=listing_id,
            )

        return install_record

    def get_installs(
        self,
        listing_id: str,
        *,
        limit: int = 100,
    ) -> list[MarketplaceInstall]:
        """Get all installs for a listing."""
        stmt = sa.select(marketplace_installs).where(
            marketplace_installs.c.listing_id == listing_id,
        ).order_by(marketplace_installs.c.installed_at.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [install_from_row(r) for r in rows]

    def get_installs_by_user(
        self,
        installer_id: str,
        *,
        limit: int = 100,
    ) -> list[MarketplaceInstall]:
        """Get all installs by a specific user."""
        stmt = sa.select(marketplace_installs).where(
            marketplace_installs.c.installer_id == installer_id,
        ).order_by(marketplace_installs.c.installed_at.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [install_from_row(r) for r in rows]
