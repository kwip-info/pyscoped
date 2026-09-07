"""Tests for ReferenceResolver."""

from __future__ import annotations

import pytest

from scoped.manifest.exceptions import ManifestLoadError
from scoped.manifest.resolver import ReferenceResolver


class TestReferenceResolver:
    def test_register_and_resolve(self):
        r = ReferenceResolver()
        r.register("principals", "admin", "id-123")

        assert r.resolve("principals", "admin") == "id-123"

    def test_resolve_missing_raises(self):
        r = ReferenceResolver()

        with pytest.raises(ManifestLoadError, match="Unresolved"):
            r.resolve("principals", "missing")

    def test_has(self):
        r = ReferenceResolver()
        r.register("scopes", "org", "id-456")

        assert r.has("scopes", "org")
        assert not r.has("scopes", "missing")

    def test_entries(self):
        r = ReferenceResolver()
        r.register("principals", "admin", "p1")
        r.register("scopes", "org", "s1")

        entries = r.entries
        assert len(entries) == 2
        assert entries[("principals", "admin")] == "p1"

    def test_overwrite(self):
        r = ReferenceResolver()
        r.register("principals", "admin", "old-id")
        r.register("principals", "admin", "new-id")

        assert r.resolve("principals", "admin") == "new-id"
