"""Tests for HMAC signing and transport client."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scoped.sync.models import SyncEntryMetadata
from scoped.sync.transport import ManagementPlaneClient

NOW = datetime(2026, 4, 1, tzinfo=timezone.utc)


@pytest.fixture
def transport():
    return ManagementPlaneClient(
        api_key="psc_live_" + "a1" * 16,
        base_url="https://api.example.com/v1",
    )


class TestKeyDerivation:
    def test_deterministic(self):
        key = "psc_live_" + "a1" * 16
        k1 = ManagementPlaneClient._derive_signing_key(key)
        k2 = ManagementPlaneClient._derive_signing_key(key)
        assert k1 == k2

    def test_different_keys_different_derivation(self):
        k1 = ManagementPlaneClient._derive_signing_key("psc_live_" + "a1" * 16)
        k2 = ManagementPlaneClient._derive_signing_key("psc_live_" + "b2" * 16)
        assert k1 != k2

    def test_returns_32_bytes(self):
        key = ManagementPlaneClient._derive_signing_key("psc_live_" + "a1" * 16)
        assert len(key) == 32  # SHA-256 digest length


class TestSigning:
    def test_sign_deterministic(self, transport):
        payload = b'{"test": "data"}'
        sig1 = transport.sign_payload(payload)
        sig2 = transport.sign_payload(payload)
        assert sig1 == sig2

    def test_sign_different_payloads(self, transport):
        sig1 = transport.sign_payload(b'{"a": 1}')
        sig2 = transport.sign_payload(b'{"a": 2}')
        assert sig1 != sig2

    def test_sign_returns_hex(self, transport):
        sig = transport.sign_payload(b"test")
        assert len(sig) == 64  # HMAC-SHA256 hex
        int(sig, 16)  # valid hex


class TestContentHash:
    def test_deterministic(self):
        entries = [
            SyncEntryMetadata(
                id="e1", sequence=1, actor_id="u", action="create",
                target_type="D", target_id="d1", timestamp=NOW, hash="h1",
            )
        ]
        h1 = ManagementPlaneClient.compute_content_hash(entries)
        h2 = ManagementPlaneClient.compute_content_hash(entries)
        assert h1 == h2

    def test_different_entries_different_hash(self):
        e1 = [SyncEntryMetadata(
            id="e1", sequence=1, actor_id="u", action="create",
            target_type="D", target_id="d1", timestamp=NOW, hash="h1",
        )]
        e2 = [SyncEntryMetadata(
            id="e2", sequence=2, actor_id="u", action="update",
            target_type="D", target_id="d1", timestamp=NOW, hash="h2",
        )]
        assert ManagementPlaneClient.compute_content_hash(e1) != \
            ManagementPlaneClient.compute_content_hash(e2)

    def test_returns_sha256_hex(self):
        entries = [SyncEntryMetadata(
            id="e1", sequence=1, actor_id="u", action="create",
            target_type="D", target_id="d1", timestamp=NOW, hash="h1",
        )]
        h = ManagementPlaneClient.compute_content_hash(entries)
        assert len(h) == 64
        int(h, 16)
