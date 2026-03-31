"""Management plane sync agent and contract models.

This package defines the complete contract between the pyscoped SDK
and the hosted management plane. Both sides import from
``scoped.sync.models`` — no contract drift.

Key classes:
    - ``SyncAgent`` — background thread that pushes audit metadata
    - ``SyncConfig`` — agent configuration
    - ``ManagementPlaneClient`` — HTTP transport with HMAC signing
    - ``SyncBatch``, ``SyncEntryMetadata``, etc. — Pydantic contract models
"""

from scoped.sync.agent import SyncAgent
from scoped.sync.config import SyncConfig
from scoped.sync.models import (
    AccountInfo,
    ApiEnvironment,
    ApiError,
    ApiKeyMetadata,
    CompatibilityCheckRequest,
    CompatibilityCheckResponse,
    CreateKeyRequest,
    CreateKeyResponse,
    ListKeysResponse,
    PingResponse,
    PlanInfoResponse,
    PlanLimits,
    ProvisionRequest,
    ProvisionResponse,
    ResourceCounts,
    RevokeKeyRequest,
    RevokeKeyResponse,
    RotateKeyRequest,
    RotateKeyResponse,
    SyncBatch,
    SyncBatchAck,
    SyncEntryMetadata,
    SyncStateSnapshot,
    SyncStatus,
    SyncVerifyRequest,
    SyncVerifyResponse,
    UsageHistoryEntry,
    UsageHistoryResponse,
    UsageSnapshot,
)
from scoped.sync.transport import ManagementPlaneClient

__all__ = [
    "AccountInfo",
    "ApiEnvironment",
    "ApiError",
    "ApiKeyMetadata",
    "CompatibilityCheckRequest",
    "CompatibilityCheckResponse",
    "CreateKeyRequest",
    "CreateKeyResponse",
    "ListKeysResponse",
    "ManagementPlaneClient",
    "PingResponse",
    "PlanInfoResponse",
    "PlanLimits",
    "ProvisionRequest",
    "ProvisionResponse",
    "ResourceCounts",
    "RevokeKeyRequest",
    "RevokeKeyResponse",
    "RotateKeyRequest",
    "RotateKeyResponse",
    "SyncAgent",
    "SyncBatch",
    "SyncBatchAck",
    "SyncConfig",
    "SyncEntryMetadata",
    "SyncStateSnapshot",
    "SyncStatus",
    "SyncVerifyRequest",
    "SyncVerifyResponse",
    "UsageHistoryEntry",
    "UsageHistoryResponse",
    "UsageSnapshot",
]
