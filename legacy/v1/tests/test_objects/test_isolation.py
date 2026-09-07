"""Tests for isolation boundary enforcement."""

from scoped.objects.isolation import can_access


class TestCanAccess:

    def test_owner_can_access(self):
        assert can_access("user-1", "user-1") is True

    def test_non_owner_denied(self):
        assert can_access("user-1", "user-2") is False

    def test_empty_ids(self):
        assert can_access("", "") is True
        assert can_access("a", "") is False
