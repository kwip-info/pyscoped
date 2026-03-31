"""Marketplace data models — listings, reviews, installs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from scoped.types import Lifecycle


class ListingType(Enum):
    """Types of marketplace listings."""

    CONNECTOR_TEMPLATE = "connector_template"
    PLUGIN = "plugin"
    INTEGRATION = "integration"


class Visibility(Enum):
    """Listing visibility levels."""

    PUBLIC = "public"
    UNLISTED = "unlisted"
    PRIVATE = "private"


@dataclass(slots=True)
class MarketplaceListing:
    """A published item in the marketplace."""

    id: str
    name: str
    publisher_id: str
    listing_type: ListingType
    published_at: datetime
    description: str = ""
    version: str = "1.0.0"
    config_template: dict[str, Any] = field(default_factory=dict)
    visibility: Visibility = Visibility.PUBLIC
    updated_at: datetime | None = None
    lifecycle: Lifecycle = Lifecycle.ACTIVE
    download_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    @property
    def is_public(self) -> bool:
        return self.visibility == Visibility.PUBLIC

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "publisher_id": self.publisher_id,
            "listing_type": self.listing_type.value,
            "version": self.version,
            "config_template": self.config_template,
            "visibility": self.visibility.value,
            "lifecycle": self.lifecycle.name,
            "download_count": self.download_count,
            "metadata": self.metadata,
            "published_at": self.published_at.isoformat(),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass(frozen=True, slots=True)
class MarketplaceReview:
    """A review of a marketplace listing."""

    id: str
    listing_id: str
    reviewer_id: str
    rating: int
    reviewed_at: datetime
    review_text: str = ""


@dataclass(frozen=True, slots=True)
class MarketplaceInstall:
    """A record of a marketplace listing being installed."""

    id: str
    listing_id: str
    installer_id: str
    installed_at: datetime
    version: str
    config: dict[str, Any] = field(default_factory=dict)
    result_ref: str | None = None
    result_type: str | None = None


# -- Row mapping helpers ---------------------------------------------------

def listing_from_row(row: dict[str, Any]) -> MarketplaceListing:
    config = row.get("config_template", "{}")
    if isinstance(config, str):
        config = json.loads(config)
    meta = row.get("metadata_json", "{}")
    if isinstance(meta, str):
        meta = json.loads(meta)
    updated = row.get("updated_at")
    return MarketplaceListing(
        id=row["id"],
        name=row["name"],
        description=row.get("description", ""),
        publisher_id=row["publisher_id"],
        listing_type=ListingType(row["listing_type"]),
        version=row.get("version", "1.0.0"),
        config_template=config,
        visibility=Visibility(row.get("visibility", "public")),
        published_at=datetime.fromisoformat(row["published_at"]),
        updated_at=datetime.fromisoformat(updated) if updated else None,
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
        download_count=row.get("download_count", 0),
        metadata=meta,
    )


def review_from_row(row: dict[str, Any]) -> MarketplaceReview:
    return MarketplaceReview(
        id=row["id"],
        listing_id=row["listing_id"],
        reviewer_id=row["reviewer_id"],
        rating=row["rating"],
        review_text=row.get("review_text", ""),
        reviewed_at=datetime.fromisoformat(row["reviewed_at"]),
    )


def install_from_row(row: dict[str, Any]) -> MarketplaceInstall:
    config = row.get("config_json", "{}")
    if isinstance(config, str):
        config = json.loads(config)
    return MarketplaceInstall(
        id=row["id"],
        listing_id=row["listing_id"],
        installer_id=row["installer_id"],
        installed_at=datetime.fromisoformat(row["installed_at"]),
        version=row["version"],
        config=config,
        result_ref=row.get("result_ref"),
        result_type=row.get("result_type"),
    )
