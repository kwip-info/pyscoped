"""Marketplace discovery — search, filter, browse, and install listings."""

from __future__ import annotations

import json
from typing import Any

from scoped.connector.marketplace.models import (
    ListingType,
    MarketplaceInstall,
    MarketplaceListing,
    Visibility,
    install_from_row,
    listing_from_row,
)
from scoped.exceptions import ListingNotFoundError, MarketplaceError
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, generate_id, now_utc


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
        clauses: list[str] = ["visibility = ?"]
        params: list[Any] = [visibility.value]

        if listing_type is not None:
            clauses.append("listing_type = ?")
            params.append(listing_type.value)
        if active_only:
            clauses.append("lifecycle = 'ACTIVE'")

        where = " WHERE " + " AND ".join(clauses)
        params.append(limit)
        rows = self._backend.fetch_all(
            f"SELECT * FROM marketplace_listings{where} ORDER BY download_count DESC, published_at DESC LIMIT ?",
            tuple(params),
        )
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
        clauses = ["(name LIKE ? OR description LIKE ?)"]
        pattern = f"%{query}%"
        params: list[Any] = [pattern, pattern]

        if listing_type is not None:
            clauses.append("listing_type = ?")
            params.append(listing_type.value)
        if active_only:
            clauses.append("lifecycle = 'ACTIVE'")

        # Public and unlisted are searchable; private are not
        clauses.append("visibility != 'private'")

        where = " WHERE " + " AND ".join(clauses)
        params.append(limit)
        rows = self._backend.fetch_all(
            f"SELECT * FROM marketplace_listings{where} ORDER BY download_count DESC LIMIT ?",
            tuple(params),
        )
        return [listing_from_row(r) for r in rows]

    def get_by_publisher(
        self,
        publisher_id: str,
        *,
        limit: int = 100,
    ) -> list[MarketplaceListing]:
        """Get all listings by a specific publisher."""
        rows = self._backend.fetch_all(
            "SELECT * FROM marketplace_listings WHERE publisher_id = ? ORDER BY published_at DESC LIMIT ?",
            (publisher_id, limit),
        )
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
        row = self._backend.fetch_one(
            "SELECT * FROM marketplace_listings WHERE id = ?", (listing_id,),
        )
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

        self._backend.execute(
            """INSERT INTO marketplace_installs
               (id, listing_id, installer_id, installed_at, version,
                config_json, result_ref, result_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (iid, listing_id, installer_id, ts.isoformat(), listing.version,
             json.dumps(cfg), result_ref, result_type),
        )

        # Increment download count
        self._backend.execute(
            "UPDATE marketplace_listings SET download_count = download_count + 1 WHERE id = ?",
            (listing_id,),
        )

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
        rows = self._backend.fetch_all(
            "SELECT * FROM marketplace_installs WHERE listing_id = ? ORDER BY installed_at DESC LIMIT ?",
            (listing_id, limit),
        )
        return [install_from_row(r) for r in rows]

    def get_installs_by_user(
        self,
        installer_id: str,
        *,
        limit: int = 100,
    ) -> list[MarketplaceInstall]:
        """Get all installs by a specific user."""
        rows = self._backend.fetch_all(
            "SELECT * FROM marketplace_installs WHERE installer_id = ? ORDER BY installed_at DESC LIMIT ?",
            (installer_id, limit),
        )
        return [install_from_row(r) for r in rows]
