"""Data models for Layer 4: Scoping & Tenancy."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import sqlalchemy as sa

from scoped.types import Lifecycle


# ---------------------------------------------------------------------------
# Role & AccessLevel enums
# ---------------------------------------------------------------------------

class ScopeRole(Enum):
    """Advisory roles for scope membership.

    The rule engine (Layer 5) determines actual permissions, but roles
    provide default semantics.
    """
    VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"
    OWNER = "owner"


class AccessLevel(Enum):
    """Access level for object projections into scopes."""
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


def coerce_role(value: str | ScopeRole) -> ScopeRole:
    """Convert a string or ScopeRole to ScopeRole.

    Raises ValueError with a descriptive message if the value is invalid.
    """
    if isinstance(value, ScopeRole):
        return value
    try:
        return ScopeRole(value)
    except ValueError:
        valid = ", ".join(repr(r.value) for r in ScopeRole)
        raise ValueError(
            f"Invalid role {value!r}. Valid roles: {valid}"
        ) from None


def coerce_access_level(value: str | AccessLevel) -> AccessLevel:
    """Convert a string or AccessLevel to AccessLevel.

    Raises ValueError with a descriptive message if the value is invalid.
    """
    if isinstance(value, AccessLevel):
        return value
    try:
        return AccessLevel(value)
    except ValueError:
        valid = ", ".join(repr(a.value) for a in AccessLevel)
        raise ValueError(
            f"Invalid access level {value!r}. Valid levels: {valid}"
        ) from None


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Scope:
    """A named isolation boundary — the sharing primitive.

    Scopes can nest (parent_scope_id).  Lifecycle states:
      ACTIVE  — normal operation
      FROZEN  — read-only, no membership / projection changes
      ARCHIVED — dissolved, all memberships/projections archived
    """
    id: str
    name: str
    owner_id: str
    created_at: datetime
    description: str = ""
    parent_scope_id: str | None = None
    registry_entry_id: str | None = None
    lifecycle: Lifecycle = Lifecycle.ACTIVE
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    @property
    def is_frozen(self) -> bool:
        return self.lifecycle == Lifecycle.DEPRECATED  # FROZEN maps to DEPRECATED

    @property
    def is_archived(self) -> bool:
        return self.lifecycle == Lifecycle.ARCHIVED

    @property
    def lifecycle_display(self) -> str:
        """Human-readable lifecycle state (uses 'FROZEN' instead of 'DEPRECATED')."""
        if self.lifecycle == Lifecycle.DEPRECATED:
            return "FROZEN"
        return self.lifecycle.name

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "owner_id": self.owner_id,
            "parent_scope_id": self.parent_scope_id,
            "registry_entry_id": self.registry_entry_id,
            "created_at": self.created_at.isoformat(),
            "lifecycle": self.lifecycle.name,
            "metadata": self.metadata,
        }


# Lifecycle name stored in DB for frozen state
SCOPE_LIFECYCLE_FROZEN = "FROZEN"


@dataclass(frozen=True, slots=True)
class ScopeMembership:
    """Binds a principal to a scope with a role."""
    id: str
    scope_id: str
    principal_id: str
    role: ScopeRole
    granted_at: datetime
    granted_by: str
    expires_at: datetime | None = None
    lifecycle: Lifecycle = Lifecycle.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    @property
    def is_expired(self) -> bool:
        """True if this membership has passed its expiration date."""
        if self.expires_at is None:
            return False
        from scoped.types import now_utc
        return now_utc() > self.expires_at

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope_id": self.scope_id,
            "principal_id": self.principal_id,
            "role": self.role.value,
            "granted_at": self.granted_at.isoformat(),
            "granted_by": self.granted_by,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "lifecycle": self.lifecycle.name,
        }


@dataclass(frozen=True, slots=True)
class ScopeProjection:
    """Makes an object visible within a scope."""
    id: str
    scope_id: str
    object_id: str
    projected_at: datetime
    projected_by: str
    access_level: AccessLevel = AccessLevel.READ
    lifecycle: Lifecycle = Lifecycle.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope_id": self.scope_id,
            "object_id": self.object_id,
            "projected_at": self.projected_at.isoformat(),
            "projected_by": self.projected_by,
            "access_level": self.access_level.value,
            "lifecycle": self.lifecycle.name,
        }


# ---------------------------------------------------------------------------
# Membership expiration helpers
# ---------------------------------------------------------------------------

def active_membership_condition(table):
    """Return SQLAlchemy condition for active, non-expired memberships.

    Checks: lifecycle == 'ACTIVE' AND (expires_at IS NULL OR expires_at > now).
    """
    from scoped.types import now_utc
    now_iso = now_utc().isoformat()
    return sa.and_(
        table.c.lifecycle == "ACTIVE",
        sa.or_(
            table.c.expires_at.is_(None),
            table.c.expires_at > now_iso,
        ),
    )


# ---------------------------------------------------------------------------
# Row mapping helpers
# ---------------------------------------------------------------------------

def _lifecycle_from_db(value: str) -> Lifecycle:
    """Map DB lifecycle strings to Lifecycle enum.

    The DB stores 'FROZEN' for frozen scopes which maps to Lifecycle.DEPRECATED.
    """
    if value == SCOPE_LIFECYCLE_FROZEN:
        return Lifecycle.DEPRECATED
    return Lifecycle[value]


def _lifecycle_to_db(lifecycle: Lifecycle) -> str:
    """Map Lifecycle enum to DB string.

    Lifecycle.DEPRECATED is stored as 'FROZEN' in the DB.
    """
    if lifecycle == Lifecycle.DEPRECATED:
        return SCOPE_LIFECYCLE_FROZEN
    return lifecycle.name


def scope_from_row(row: dict[str, Any]) -> Scope:
    meta_raw = row.get("metadata_json", "{}")
    return Scope(
        id=row["id"],
        name=row["name"],
        description=row.get("description", ""),
        owner_id=row["owner_id"],
        parent_scope_id=row.get("parent_scope_id"),
        registry_entry_id=row.get("registry_entry_id"),
        created_at=datetime.fromisoformat(row["created_at"]),
        lifecycle=_lifecycle_from_db(row["lifecycle"]),
        metadata=json.loads(meta_raw) if meta_raw else {},
    )


def membership_from_row(row: dict[str, Any]) -> ScopeMembership:
    expires = row.get("expires_at")
    return ScopeMembership(
        id=row["id"],
        scope_id=row["scope_id"],
        principal_id=row["principal_id"],
        role=ScopeRole(row["role"]),
        granted_at=datetime.fromisoformat(row["granted_at"]),
        granted_by=row["granted_by"],
        expires_at=datetime.fromisoformat(expires) if expires else None,
        lifecycle=Lifecycle[row["lifecycle"]],
    )


def projection_from_row(row: dict[str, Any]) -> ScopeProjection:
    return ScopeProjection(
        id=row["id"],
        scope_id=row["scope_id"],
        object_id=row["object_id"],
        projected_at=datetime.fromisoformat(row["projected_at"]),
        projected_by=row["projected_by"],
        access_level=AccessLevel(row["access_level"]),
        lifecycle=Lifecycle[row["lifecycle"]],
    )
