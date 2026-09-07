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
from scoped.exceptions import (
    AccessDeniedError,
    ListingNotFoundError,
    MarketplaceError,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import marketplace_installs, marketplace_listings
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, generate_id, now_utc
from scoped._stability import stable


@stable(since="1.8.0")
class MarketplaceDiscovery:
    """Search, filter, browse, and install marketplace listings."""

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

        Visibility enforcement: ``PRIVATE`` listings can only be
        installed by their publisher. ``PUBLIC`` and ``UNLISTED``
        listings are installable by anyone with the listing ID
        (the unlisted-by-link semantic).
        """
        # Layer 5 rule gate before any work happens.
        self._check_rule(
            action="marketplace_install",
            principal_id=installer_id,
            object_id=listing_id,
        )

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

        # Visibility gate: PRIVATE = publisher-only.
        if (
            listing.visibility == Visibility.PRIVATE
            and listing.publisher_id != installer_id
        ):
            raise AccessDeniedError(
                f"Listing '{listing_id}' is private and can only be "
                f"installed by its publisher",
                context={
                    "listing_id": listing_id,
                    "installer_id": installer_id,
                    "publisher_id": listing.publisher_id,
                },
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
                target_type="marketplace_install",
                target_id=iid,
                metadata={
                    "listing_id": listing_id,
                    "version": listing.version,
                },
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
