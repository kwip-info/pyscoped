"""Tests for ScopedObject, ObjectVersion, Tombstone, and compute_checksum."""

from scoped.objects.models import (
    ObjectVersion,
    ScopedObject,
    Tombstone,
    compute_checksum,
)
from scoped.types import Lifecycle, generate_id, now_utc


class TestComputeChecksum:

    def test_deterministic(self):
        data = {"title": "Hello", "body": "World"}
        assert compute_checksum(data) == compute_checksum(data)

    def test_key_order_irrelevant(self):
        a = compute_checksum({"a": 1, "b": 2})
        b = compute_checksum({"b": 2, "a": 1})
        assert a == b

    def test_different_data_different_checksum(self):
        a = compute_checksum({"x": 1})
        b = compute_checksum({"x": 2})
        assert a != b

    def test_sha256_length(self):
        assert len(compute_checksum({"k": "v"})) == 64


class TestScopedObject:

    def test_snapshot(self):
        ts = now_utc()
        obj = ScopedObject(
            id="obj-1",
            object_type="Document",
            owner_id="user-1",
            current_version=3,
            created_at=ts,
            lifecycle=Lifecycle.ACTIVE,
            registry_entry_id="reg-1",
        )
        snap = obj.snapshot()
        assert snap["id"] == "obj-1"
        assert snap["object_type"] == "Document"
        assert snap["owner_id"] == "user-1"
        assert snap["current_version"] == 3
        assert snap["lifecycle"] == "ACTIVE"
        assert snap["registry_entry_id"] == "reg-1"

    def test_is_active(self):
        obj = ScopedObject(
            id="x", object_type="X", owner_id="u",
            current_version=1, created_at=now_utc(),
            lifecycle=Lifecycle.ACTIVE,
        )
        assert obj.is_active
        assert not obj.is_tombstoned

    def test_is_tombstoned(self):
        obj = ScopedObject(
            id="x", object_type="X", owner_id="u",
            current_version=1, created_at=now_utc(),
            lifecycle=Lifecycle.ARCHIVED,
        )
        assert obj.is_tombstoned
        assert not obj.is_active


class TestObjectVersion:

    def test_snapshot(self):
        ts = now_utc()
        ver = ObjectVersion(
            id="v1", object_id="obj-1", version=2,
            data={"title": "Test"}, created_at=ts,
            created_by="user-1", change_reason="updated title",
            checksum="abc123",
        )
        snap = ver.snapshot()
        assert snap["version"] == 2
        assert snap["data"] == {"title": "Test"}
        assert snap["change_reason"] == "updated title"
        assert snap["checksum"] == "abc123"

    def test_frozen(self):
        ver = ObjectVersion(
            id="v1", object_id="obj-1", version=1,
            data={}, created_at=now_utc(), created_by="u",
        )
        import pytest
        with pytest.raises(AttributeError):
            ver.version = 2  # type: ignore[misc]


class TestTombstone:

    def test_fields(self):
        ts = now_utc()
        tomb = Tombstone(
            id="t1", object_id="obj-1",
            tombstoned_at=ts, tombstoned_by="user-1",
            reason="no longer needed",
        )
        assert tomb.object_id == "obj-1"
        assert tomb.reason == "no longer needed"

    def test_frozen(self):
        tomb = Tombstone(
            id="t1", object_id="obj-1",
            tombstoned_at=now_utc(), tombstoned_by="u",
        )
        import pytest
        with pytest.raises(AttributeError):
            tomb.reason = "changed"  # type: ignore[misc]
