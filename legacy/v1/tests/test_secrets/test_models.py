"""Tests for secret data models."""

from datetime import timedelta

from scoped.secrets.models import (
    AccessResult,
    Secret,
    SecretAccessEntry,
    SecretClassification,
    SecretPolicy,
    SecretRef,
    SecretVersion,
)
from scoped.types import Lifecycle, now_utc


class TestSecret:

    def test_snapshot(self):
        ts = now_utc()
        s = Secret(
            id="s1", name="api-key", owner_id="alice", object_id="obj1",
            created_at=ts, classification=SecretClassification.SENSITIVE,
            description="Stripe key",
        )
        snap = s.snapshot()
        assert snap["id"] == "s1"
        assert snap["name"] == "api-key"
        assert snap["classification"] == "sensitive"
        assert snap["description"] == "Stripe key"
        assert "encrypted" not in str(snap).lower()
        assert "value" not in str(snap).lower()

    def test_is_active(self):
        s = Secret(id="s1", name="k", owner_id="u", object_id="o", created_at=now_utc())
        assert s.is_active
        s.lifecycle = Lifecycle.ARCHIVED
        assert not s.is_active

    def test_snapshot_no_expiry(self):
        s = Secret(id="s1", name="k", owner_id="u", object_id="o", created_at=now_utc())
        snap = s.snapshot()
        assert snap["expires_at"] is None
        assert snap["last_rotated_at"] is None


class TestSecretVersion:

    def test_snapshot_excludes_value(self):
        ts = now_utc()
        v = SecretVersion(
            id="v1", secret_id="s1", version=1,
            encrypted_value="ENC:ciphertext", encryption_algo="fernet",
            key_id="k1", created_at=ts, created_by="alice",
        )
        snap = v.snapshot()
        assert "encrypted_value" not in snap
        assert snap["version"] == 1
        assert snap["encryption_algo"] == "fernet"
        assert snap["key_id"] == "k1"


class TestSecretRef:

    def test_snapshot(self):
        ts = now_utc()
        r = SecretRef(
            id="r1", secret_id="s1", ref_token="tok123",
            granted_to="bob", granted_at=ts, granted_by="alice",
            scope_id="scope1",
        )
        snap = r.snapshot()
        assert snap["granted_to"] == "bob"
        assert snap["scope_id"] == "scope1"

    def test_is_active(self):
        r = SecretRef(
            id="r1", secret_id="s1", ref_token="tok",
            granted_to="u", granted_at=now_utc(), granted_by="u",
        )
        assert r.is_active
        r.lifecycle = Lifecycle.ARCHIVED
        assert not r.is_active

    def test_is_expired(self):
        past = now_utc() - timedelta(hours=1)
        future = now_utc() + timedelta(hours=1)
        r = SecretRef(
            id="r1", secret_id="s1", ref_token="tok",
            granted_to="u", granted_at=now_utc(), granted_by="u",
            expires_at=past,
        )
        assert r.is_expired(now_utc())

        r2 = SecretRef(
            id="r2", secret_id="s1", ref_token="tok2",
            granted_to="u", granted_at=now_utc(), granted_by="u",
            expires_at=future,
        )
        assert not r2.is_expired(now_utc())

    def test_no_expiry_never_expires(self):
        r = SecretRef(
            id="r1", secret_id="s1", ref_token="tok",
            granted_to="u", granted_at=now_utc(), granted_by="u",
        )
        assert not r.is_expired(now_utc())


class TestSecretPolicy:

    def test_snapshot(self):
        ts = now_utc()
        p = SecretPolicy(
            id="p1", created_at=ts, created_by="alice",
            classification="critical", max_age_seconds=86400,
            auto_rotate=True, allowed_scopes=["s1", "s2"],
        )
        snap = p.snapshot()
        assert snap["classification"] == "critical"
        assert snap["max_age_seconds"] == 86400
        assert snap["auto_rotate"] is True
        assert snap["allowed_scopes"] == ["s1", "s2"]


class TestEnums:

    def test_classifications(self):
        assert SecretClassification.STANDARD.value == "standard"
        assert SecretClassification.SENSITIVE.value == "sensitive"
        assert SecretClassification.CRITICAL.value == "critical"

    def test_access_results(self):
        assert AccessResult.SUCCESS.value == "success"
        assert AccessResult.DENIED.value == "denied"
        assert AccessResult.EXPIRED.value == "expired"
