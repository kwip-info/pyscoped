"""Tests for version diffing."""

from scoped.objects.models import ObjectVersion
from scoped.objects.versioning import diff_versions
from scoped.types import now_utc


def _ver(version: int, data: dict) -> ObjectVersion:
    return ObjectVersion(
        id=f"v{version}", object_id="obj-1", version=version,
        data=data, created_at=now_utc(), created_by="u",
    )


class TestDiffVersions:

    def test_no_changes(self):
        v1 = _ver(1, {"a": 1, "b": 2})
        v2 = _ver(2, {"a": 1, "b": 2})
        d = diff_versions(v1, v2)
        assert d["added"] == {}
        assert d["removed"] == {}
        assert d["changed"] == {}

    def test_added_field(self):
        v1 = _ver(1, {"a": 1})
        v2 = _ver(2, {"a": 1, "b": 2})
        d = diff_versions(v1, v2)
        assert d["added"] == {"b": 2}
        assert d["removed"] == {}
        assert d["changed"] == {}

    def test_removed_field(self):
        v1 = _ver(1, {"a": 1, "b": 2})
        v2 = _ver(2, {"a": 1})
        d = diff_versions(v1, v2)
        assert d["removed"] == {"b": 2}
        assert d["added"] == {}

    def test_changed_field(self):
        v1 = _ver(1, {"title": "Old"})
        v2 = _ver(2, {"title": "New"})
        d = diff_versions(v1, v2)
        assert d["changed"] == {"title": {"old": "Old", "new": "New"}}

    def test_mixed_changes(self):
        v1 = _ver(1, {"a": 1, "b": 2, "c": 3})
        v2 = _ver(2, {"a": 10, "c": 3, "d": 4})
        d = diff_versions(v1, v2)
        assert d["changed"] == {"a": {"old": 1, "new": 10}}
        assert d["removed"] == {"b": 2}
        assert d["added"] == {"d": 4}
