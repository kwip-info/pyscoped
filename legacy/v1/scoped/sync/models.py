"""Management plane contract — Pydantic models shared by SDK and server.

This module defines every data structure that crosses the wire between
the pyscoped SDK (running in customer infrastructure) and the hosted
management plane (our service). Both sides import from here —
no contract drift.

Five contract areas:
    1. Account & Provisioning — signup, account info
    2. API Key Management — create, list, revoke, rotate keys
    3. Sync — audit metadata batches, watermarks, verification
    4. Billing & Usage — resource counts, plan limits, usage history
    5. Health & Status — ping, SDK compatibility

Security invariant:
    ``SyncEntryMetadata`` deliberately excludes ``before_state`` and
    ``after_state``. Customer data **never** leaves their infrastructure.
    Only structural metadata (who did what, when, to what type) is synced.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# =========================================================================
# Shared enums
# =========================================================================

class ApiEnvironment(str, Enum):
    """API key environment — determines billing and sync target."""
    LIVE = "live"
    TEST = "test"


class SyncStatus(str, Enum):
    """Sync agent lifecycle state."""
    IDLE = "idle"
    SYNCING = "syncing"
    PAUSED = "paused"
    ERROR = "error"
    STOPPED = "stopped"


# =========================================================================
# Area 1: Account & Provisioning
# =========================================================================

class ProvisionRequest(BaseModel):
    """SDK → API: New customer signup.

    Sent when a developer calls ``scoped.provision(email="...")``.
    The management plane creates an account, sends a verification
    email, and returns an API key.
    """
    model_config = ConfigDict(frozen=True)

    email: str
    sdk_version: str
    project_name: str | None = None
    environment: ApiEnvironment = ApiEnvironment.LIVE


class ProvisionResponse(BaseModel):
    """API → SDK: Account created, pending verification."""
    model_config = ConfigDict(frozen=True)

    account_id: str
    api_key: str
    status: str
    verification_url: str | None = None
    message: str


class AccountInfo(BaseModel):
    """API → SDK: Current account details."""
    model_config = ConfigDict(frozen=True)

    account_id: str
    email: str
    plan: str
    status: str
    created_at: datetime
    api_environment: ApiEnvironment
    limits: PlanLimits


# =========================================================================
# Area 2: API Key Management
# =========================================================================

class ApiKeyMetadata(BaseModel):
    """Metadata for a single API key.

    The ``key_prefix`` shows the first 12 characters only (e.g.
    ``"psc_live_a1b2"``). The full key is never returned after creation.
    """
    model_config = ConfigDict(frozen=True)

    key_id: str
    key_prefix: str
    environment: ApiEnvironment
    label: str = ""
    is_active: bool = True
    created_at: datetime
    last_used_at: datetime | None = None


class CreateKeyRequest(BaseModel):
    """SDK → API: Create a new API key."""
    model_config = ConfigDict(frozen=True)

    environment: ApiEnvironment = ApiEnvironment.LIVE
    label: str = ""


class CreateKeyResponse(BaseModel):
    """API → SDK: New key created.

    The ``api_key`` field contains the full key. This is the only time
    the full key is returned — it must be stored by the customer.
    """
    model_config = ConfigDict(frozen=True)

    key_id: str
    api_key: str
    environment: ApiEnvironment
    created_at: datetime


class ListKeysResponse(BaseModel):
    """API → SDK: All keys for the account."""
    model_config = ConfigDict(frozen=True)

    keys: list[ApiKeyMetadata]


class RevokeKeyRequest(BaseModel):
    """SDK → API: Revoke an API key."""
    model_config = ConfigDict(frozen=True)

    key_id: str


class RevokeKeyResponse(BaseModel):
    """API → SDK: Key revoked."""
    model_config = ConfigDict(frozen=True)

    key_id: str
    revoked_at: datetime


class RotateKeyRequest(BaseModel):
    """SDK → API: Rotate a key (atomic create-new + revoke-old)."""
    model_config = ConfigDict(frozen=True)

    key_id: str
    label: str = ""


class RotateKeyResponse(BaseModel):
    """API → SDK: Key rotated.

    ``new_api_key`` is the full new key (shown once).
    """
    model_config = ConfigDict(frozen=True)

    old_key_id: str
    old_revoked_at: datetime
    new_key_id: str
    new_api_key: str
    new_created_at: datetime


# =========================================================================
# Area 3: Sync
# =========================================================================

class SyncEntryMetadata(BaseModel):
    """A single audit entry stripped of data payloads.

    **CRITICAL**: This model deliberately excludes ``before_state`` and
    ``after_state``. Customer data never leaves their infrastructure.
    Only structural metadata is synced: who did what, when, to what
    type of thing.

    Fields mirror ``TraceEntry`` from ``scoped.audit.models`` minus
    the state fields.
    """
    model_config = ConfigDict(frozen=True)

    id: str
    sequence: int
    actor_id: str
    action: str
    target_type: str
    target_id: str
    timestamp: datetime
    hash: str
    previous_hash: str = ""
    scope_id: str | None = None
    parent_trace_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResourceCounts(BaseModel):
    """Active resource counts at time of sync.

    Used for usage-based billing. Counts are snapshots, not cumulative.
    """
    model_config = ConfigDict(frozen=True)

    active_objects: int
    active_principals: int
    active_scopes: int
    timestamp: datetime


class SyncBatch(BaseModel):
    """SDK → API: A batch of audit metadata + resource counts.

    The atomic unit of sync. Each batch carries:
    - Audit entries (metadata only, no data payloads)
    - Resource counts for billing metering
    - Chain integrity hashes (ties to tamper-evident audit chain)
    - HMAC signature for authenticity

    The management plane deduplicates by sequence number. The hash
    chain provides exactly-once delivery guarantees without requiring
    an external message broker.
    """
    model_config = ConfigDict(frozen=True)

    batch_id: str
    sdk_version: str
    entries: list[SyncEntryMetadata]
    resource_counts: ResourceCounts
    first_sequence: int
    last_sequence: int
    chain_hash: str
    content_hash: str
    signature: str
    created_at: datetime


class SyncBatchAck(BaseModel):
    """API → SDK: Acknowledgment of a received batch.

    On ``accepted=True``, the SDK advances its watermark to
    ``server_sequence``. On ``accepted=False``, the ``errors`` list
    explains why.
    """
    model_config = ConfigDict(frozen=True)

    batch_id: str
    accepted: bool
    server_sequence: int
    server_chain_hash: str
    message: str = ""
    errors: list[str] = Field(default_factory=list)


class SyncVerifyRequest(BaseModel):
    """SDK → API: Verify chain integrity between local and server."""
    model_config = ConfigDict(frozen=True)

    from_sequence: int = 1
    to_sequence: int | None = None
    local_chain_hash: str
    local_entry_count: int


class SyncVerifyResponse(BaseModel):
    """API → SDK: Verification result.

    ``verified=True`` means the local and server hash chains match
    perfectly. On mismatch, ``first_mismatch_sequence`` indicates
    where they diverge.
    """
    model_config = ConfigDict(frozen=True)

    verified: bool
    local_chain_hash: str
    server_chain_hash: str
    local_entry_count: int
    server_entry_count: int
    first_mismatch_sequence: int | None = None
    message: str = ""


class SyncStateSnapshot(BaseModel):
    """Local sync state (mirrors ``_sync_state`` table row).

    Returned by ``client.sync_status()``. Persisted in the customer's
    database alongside their data — participates in the same
    backup/restore.
    """
    model_config = ConfigDict(frozen=True)

    last_sequence: int = 0
    last_hash: str = ""
    last_synced_at: datetime | None = None
    last_batch_id: str | None = None
    status: SyncStatus = SyncStatus.IDLE
    error_message: str | None = None
    error_count: int = 0


# =========================================================================
# Area 4: Billing & Usage
# =========================================================================

class PlanLimits(BaseModel):
    """Limits for the current plan tier.

    The SDK never enforces these — the management plane does.
    These are informational for the customer.
    """
    model_config = ConfigDict(frozen=True)

    max_objects: int
    max_principals: int
    audit_retention_days: int
    min_sync_interval_seconds: int
    tier: str


class UsageSnapshot(BaseModel):
    """Current billing-period usage.

    ``peak_*`` values are the high-water marks for the billing period.
    These determine the bill, not current values.
    """
    model_config = ConfigDict(frozen=True)

    period_start: datetime
    period_end: datetime
    peak_objects: int
    peak_principals: int
    current_objects: int
    current_principals: int
    audit_entries_synced: int
    last_sync_at: datetime | None = None


class UsageHistoryEntry(BaseModel):
    """A single historical usage data point."""
    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    active_objects: int
    active_principals: int
    active_scopes: int
    audit_entries_synced: int


class UsageHistoryResponse(BaseModel):
    """API → SDK: Historical usage data."""
    model_config = ConfigDict(frozen=True)

    entries: list[UsageHistoryEntry]
    period_start: datetime
    period_end: datetime
    granularity: str


class PlanInfoResponse(BaseModel):
    """API → SDK: Current plan details + usage."""
    model_config = ConfigDict(frozen=True)

    plan: str
    limits: PlanLimits
    usage: UsageSnapshot
    overage_allowed: bool = False


# =========================================================================
# Area 5: Health & Status
# =========================================================================

class PingResponse(BaseModel):
    """API → SDK: Management plane health check."""
    model_config = ConfigDict(frozen=True)

    ok: bool
    server_time: datetime
    api_version: str


class CompatibilityCheckRequest(BaseModel):
    """SDK → API: Check if this SDK version is supported."""
    model_config = ConfigDict(frozen=True)

    sdk_version: str
    python_version: str


class CompatibilityCheckResponse(BaseModel):
    """API → SDK: Compatibility result."""
    model_config = ConfigDict(frozen=True)

    compatible: bool
    sdk_version: str
    min_supported_sdk: str
    latest_sdk: str
    message: str = ""
    deprecation_warning: str | None = None


# =========================================================================
# Shared: API error envelope
# =========================================================================

class ApiError(BaseModel):
    """Standard error response from the management plane.

    All non-2xx responses use this envelope.
    """
    model_config = ConfigDict(frozen=True)

    error: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


# -------------------------------------------------------------------------
# Forward reference resolution (AccountInfo references PlanLimits)
# -------------------------------------------------------------------------

AccountInfo.model_rebuild()
