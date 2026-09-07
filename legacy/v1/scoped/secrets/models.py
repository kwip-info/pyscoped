"""Secret data models — secrets, versions, refs, policies, access log."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from scoped.types import Lifecycle


class SecretClassification(Enum):
    """How sensitive a secret is."""

    STANDARD = "standard"
    SENSITIVE = "sensitive"
    CRITICAL = "critical"


class AccessResult(Enum):
    """Outcome of a secret access attempt."""

    SUCCESS = "success"
    DENIED = "denied"
    EXPIRED = "expired"


@dataclass(slots=True)
class Secret:
    """A secret with encrypted value, versioned and scoped."""

    id: str
    name: str
    owner_id: str
    object_id: str
    created_at: datetime
    current_version: int = 1
    classification: SecretClassification = SecretClassification.STANDARD
    description: str = ""
    expires_at: datetime | None = None
    last_rotated_at: datetime | None = None
    lifecycle: Lifecycle = Lifecycle.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    def snapshot(self) -> dict[str, Any]:
        """Snapshot never includes secret values."""
        return {
            "id": self.id,
            "name": self.name,
            "owner_id": self.owner_id,
            "object_id": self.object_id,
            "current_version": self.current_version,
            "classification": self.classification.value,
            "description": self.description,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_rotated_at": self.last_rotated_at.isoformat() if self.last_rotated_at else None,
            "lifecycle": self.lifecycle.name,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SecretVersion:
    """An encrypted version of a secret value."""

    id: str
    secret_id: str
    version: int
    encrypted_value: str
    encryption_algo: str
    key_id: str
    created_at: datetime
    created_by: str
    reason: str = ""

    def snapshot(self) -> dict[str, Any]:
        """Snapshot never includes encrypted value."""
        return {
            "id": self.id,
            "secret_id": self.secret_id,
            "version": self.version,
            "encryption_algo": self.encryption_algo,
            "key_id": self.key_id,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "reason": self.reason,
        }


@dataclass(slots=True)
class SecretRef:
    """An opaque reference handle to a secret."""

    id: str
    secret_id: str
    ref_token: str
    granted_to: str
    granted_at: datetime
    granted_by: str
    scope_id: str | None = None
    environment_id: str | None = None
    expires_at: datetime | None = None
    lifecycle: Lifecycle = Lifecycle.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    def is_expired(self, now: datetime) -> bool:
        if self.expires_at is None:
            return False
        return now >= self.expires_at

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "secret_id": self.secret_id,
            "ref_token": self.ref_token,
            "granted_to": self.granted_to,
            "scope_id": self.scope_id,
            "environment_id": self.environment_id,
            "granted_at": self.granted_at.isoformat(),
            "granted_by": self.granted_by,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "lifecycle": self.lifecycle.name,
        }


@dataclass(frozen=True, slots=True)
class SecretAccessEntry:
    """Record of a secret access attempt."""

    id: str
    secret_id: str
    accessor_id: str
    access_type: str
    accessed_at: datetime
    result: AccessResult
    ref_id: str | None = None
    environment_id: str | None = None
    scope_id: str | None = None


@dataclass(slots=True)
class SecretPolicy:
    """Policy governing secret lifecycle and access restrictions."""

    id: str
    created_at: datetime
    created_by: str
    secret_id: str | None = None
    classification: str | None = None
    max_age_seconds: int | None = None
    auto_rotate: bool = False
    allowed_scopes: list[str] = field(default_factory=list)
    allowed_envs: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "secret_id": self.secret_id,
            "classification": self.classification,
            "max_age_seconds": self.max_age_seconds,
            "auto_rotate": self.auto_rotate,
            "allowed_scopes": self.allowed_scopes,
            "allowed_envs": self.allowed_envs,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
        }


# -- Row mapping helpers ---------------------------------------------------

def secret_from_row(row: dict[str, Any]) -> Secret:
    expires = row.get("expires_at")
    rotated = row.get("last_rotated_at")
    return Secret(
        id=row["id"],
        name=row["name"],
        owner_id=row["owner_id"],
        object_id=row["object_id"],
        current_version=row.get("current_version", 1),
        classification=SecretClassification(row.get("classification", "standard")),
        description=row.get("description", ""),
        expires_at=datetime.fromisoformat(expires) if expires else None,
        last_rotated_at=datetime.fromisoformat(rotated) if rotated else None,
        lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def version_from_row(row: dict[str, Any]) -> SecretVersion:
    return SecretVersion(
        id=row["id"],
        secret_id=row["secret_id"],
        version=row["version"],
        encrypted_value=row["encrypted_value"],
        encryption_algo=row.get("encryption_algo", "fernet"),
        key_id=row["key_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        created_by=row["created_by"],
        reason=row.get("reason", ""),
    )


_LIFECYCLE_MAP = {v.name: v for v in Lifecycle}
_LIFECYCLE_MAP["REVOKED"] = Lifecycle.ARCHIVED  # refs use REVOKED, map to ARCHIVED


def _parse_lifecycle(raw: str) -> Lifecycle:
    return _LIFECYCLE_MAP.get(raw, Lifecycle.ACTIVE)


def ref_from_row(row: dict[str, Any]) -> SecretRef:
    granted = row.get("granted_at")
    expires = row.get("expires_at")
    raw_lifecycle = row.get("lifecycle", "ACTIVE")
    # Refs use REVOKED in DB — treat as inactive
    if raw_lifecycle == "REVOKED":
        lifecycle = Lifecycle.ARCHIVED
    else:
        lifecycle = Lifecycle[raw_lifecycle]
    return SecretRef(
        id=row["id"],
        secret_id=row["secret_id"],
        ref_token=row["ref_token"],
        granted_to=row["granted_to"],
        scope_id=row.get("scope_id"),
        environment_id=row.get("environment_id"),
        granted_at=datetime.fromisoformat(granted) if granted else None,
        granted_by=row["granted_by"],
        expires_at=datetime.fromisoformat(expires) if expires else None,
        lifecycle=lifecycle,
    )


def access_entry_from_row(row: dict[str, Any]) -> SecretAccessEntry:
    return SecretAccessEntry(
        id=row["id"],
        secret_id=row["secret_id"],
        ref_id=row.get("ref_id"),
        accessor_id=row["accessor_id"],
        access_type=row["access_type"],
        accessed_at=datetime.fromisoformat(row["accessed_at"]),
        environment_id=row.get("environment_id"),
        scope_id=row.get("scope_id"),
        result=AccessResult(row.get("result", "success")),
    )


def policy_from_row(row: dict[str, Any]) -> SecretPolicy:
    scopes = row.get("allowed_scopes", "[]")
    if isinstance(scopes, str):
        scopes = json.loads(scopes)
    envs = row.get("allowed_envs", "[]")
    if isinstance(envs, str):
        envs = json.loads(envs)
    return SecretPolicy(
        id=row["id"],
        secret_id=row.get("secret_id"),
        classification=row.get("classification"),
        max_age_seconds=row.get("max_age_seconds"),
        auto_rotate=bool(row.get("auto_rotate", 0)),
        allowed_scopes=scopes,
        allowed_envs=envs,
        created_at=datetime.fromisoformat(row["created_at"]),
        created_by=row["created_by"],
    )
