"""Tests for Pydantic contract models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from scoped.sync.models import (
    ApiEnvironment,
    ApiError,
    ApiKeyMetadata,
    CompatibilityCheckRequest,
    CompatibilityCheckResponse,
    CreateKeyRequest,
    CreateKeyResponse,
    PingResponse,
    PlanLimits,
    ProvisionRequest,
    ProvisionResponse,
    ResourceCounts,
    RevokeKeyRequest,
    RotateKeyResponse,
    SyncBatch,
    SyncBatchAck,
    SyncEntryMetadata,
    SyncStateSnapshot,
    SyncStatus,
    SyncVerifyRequest,
    SyncVerifyResponse,
    UsageHistoryEntry,
    UsageSnapshot,
)

NOW = datetime(2026, 4, 1, tzinfo=timezone.utc)


class TestSyncEntryMetadata:
    def test_round_trip(self):
        entry = SyncEntryMetadata(
            id="abc",
            sequence=1,
            actor_id="user-1",
            action="create",
            target_type="Document",
            target_id="doc-1",
            timestamp=NOW,
            hash="a" * 64,
            previous_hash="",
        )
        data = entry.model_dump(mode="json")
        restored = SyncEntryMetadata.model_validate(data)
        assert restored.id == "abc"
        assert restored.sequence == 1

    def test_no_before_after_state_fields(self):
        """Data never leaves customer infra — these fields must not exist."""
        entry = SyncEntryMetadata(
            id="x", sequence=1, actor_id="a", action="create",
            target_type="Doc", target_id="d", timestamp=NOW, hash="h",
        )
        fields = set(SyncEntryMetadata.model_fields.keys())
        assert "before_state" not in fields
        assert "after_state" not in fields

    def test_frozen(self):
        entry = SyncEntryMetadata(
            id="x", sequence=1, actor_id="a", action="create",
            target_type="Doc", target_id="d", timestamp=NOW, hash="h",
        )
        with pytest.raises(ValidationError):
            entry.id = "changed"

    def test_optional_fields(self):
        entry = SyncEntryMetadata(
            id="x", sequence=1, actor_id="a", action="create",
            target_type="Doc", target_id="d", timestamp=NOW, hash="h",
        )
        assert entry.scope_id is None
        assert entry.parent_trace_id is None
        assert entry.metadata == {}


class TestSyncBatch:
    def test_round_trip(self):
        entry = SyncEntryMetadata(
            id="e1", sequence=1, actor_id="u", action="create",
            target_type="D", target_id="d1", timestamp=NOW, hash="h1",
        )
        counts = ResourceCounts(
            active_objects=10, active_principals=3,
            active_scopes=2, timestamp=NOW,
        )
        batch = SyncBatch(
            batch_id="b1", sdk_version="0.4.0",
            entries=[entry], resource_counts=counts,
            first_sequence=1, last_sequence=1,
            chain_hash="h1", content_hash="ch",
            signature="sig", created_at=NOW,
        )
        data = batch.model_dump(mode="json")
        restored = SyncBatch.model_validate(data)
        assert restored.batch_id == "b1"
        assert len(restored.entries) == 1


class TestSyncBatchAck:
    def test_accepted(self):
        ack = SyncBatchAck(
            batch_id="b1", accepted=True,
            server_sequence=100, server_chain_hash="h",
        )
        assert ack.accepted is True
        assert ack.errors == []

    def test_rejected_with_errors(self):
        ack = SyncBatchAck(
            batch_id="b1", accepted=False,
            server_sequence=50, server_chain_hash="h",
            errors=["duplicate batch"],
        )
        assert ack.accepted is False
        assert len(ack.errors) == 1


class TestSyncStateSnapshot:
    def test_defaults(self):
        state = SyncStateSnapshot()
        assert state.last_sequence == 0
        assert state.status == SyncStatus.IDLE
        assert state.error_count == 0


class TestAccountModels:
    def test_provision_request(self):
        req = ProvisionRequest(email="dev@co.com", sdk_version="0.4.0")
        assert req.environment == ApiEnvironment.LIVE

    def test_provision_response(self):
        resp = ProvisionResponse(
            account_id="acc-1", api_key="psc_live_" + "a1" * 16,
            status="pending_verification", message="Check your email",
        )
        assert resp.account_id == "acc-1"


class TestApiKeyModels:
    def test_key_metadata(self):
        meta = ApiKeyMetadata(
            key_id="k1", key_prefix="psc_live_a1b2",
            environment=ApiEnvironment.LIVE, created_at=NOW,
        )
        assert meta.is_active is True
        assert meta.key_prefix.startswith("psc_live_")

    def test_create_key(self):
        req = CreateKeyRequest(environment=ApiEnvironment.TEST, label="CI key")
        assert req.label == "CI key"

    def test_rotate_response(self):
        resp = RotateKeyResponse(
            old_key_id="k1", old_revoked_at=NOW,
            new_key_id="k2", new_api_key="psc_live_" + "b2" * 16,
            new_created_at=NOW,
        )
        assert resp.old_key_id != resp.new_key_id


class TestBillingModels:
    def test_plan_limits(self):
        limits = PlanLimits(
            max_objects=1000, max_principals=50,
            audit_retention_days=90, min_sync_interval_seconds=60,
            tier="pro",
        )
        assert limits.tier == "pro"

    def test_usage_snapshot(self):
        usage = UsageSnapshot(
            period_start=NOW, period_end=NOW,
            peak_objects=342, peak_principals=28,
            current_objects=300, current_principals=25,
            audit_entries_synced=14523,
        )
        assert usage.peak_objects > usage.current_objects


class TestHealthModels:
    def test_ping(self):
        resp = PingResponse(ok=True, server_time=NOW, api_version="2026-04-01")
        assert resp.ok is True

    def test_compatibility(self):
        req = CompatibilityCheckRequest(sdk_version="0.4.0", python_version="3.13")
        resp = CompatibilityCheckResponse(
            compatible=True, sdk_version="0.4.0",
            min_supported_sdk="0.3.0", latest_sdk="0.4.0",
        )
        assert resp.compatible is True


class TestApiError:
    def test_error_envelope(self):
        err = ApiError(error="rate_limited", message="Too many requests")
        assert err.details == {}
        assert err.request_id is None
