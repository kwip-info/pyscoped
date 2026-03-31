"""Tests for RegistryIntrospector."""

from __future__ import annotations

from scoped.testing.introspection import RegistryIntrospector
from scoped.types import generate_id, now_utc


def _setup_registry_entry(backend, *, name="test", kind="MODEL", namespace="test") -> str:
    ts = now_utc().isoformat()
    eid = generate_id()
    backend.execute(
        "INSERT INTO registry_entries "
        "(id, urn, kind, namespace, name, registered_at, registered_by) "
        "VALUES (?, ?, ?, ?, ?, ?, 'system')",
        (eid, f"scoped:{kind}:{namespace}:{name}:1", kind, namespace, name, ts),
    )
    return eid


def _setup_principal(backend, *, registry_entry_id=None) -> str:
    pid = generate_id()
    ts = now_utc().isoformat()
    reg_id = registry_entry_id or _setup_registry_entry(backend)
    backend.execute(
        "INSERT INTO principals (id, kind, display_name, registry_entry_id, created_at) "
        "VALUES (?, 'user', 'Test', ?, ?)",
        (pid, reg_id, ts),
    )
    return pid


class TestRegistryIntrospector:
    def test_scan_empty(self, sqlite_backend):
        intro = RegistryIntrospector(sqlite_backend)
        result = intro.scan()

        assert result.total_entries == 0
        assert result.is_clean

    def test_scan_with_entries(self, sqlite_backend):
        _setup_registry_entry(sqlite_backend, name="a")
        _setup_registry_entry(sqlite_backend, name="b", kind="FUNCTION")

        intro = RegistryIntrospector(sqlite_backend)
        result = intro.scan()

        assert result.total_entries == 2
        assert result.by_kind["MODEL"] == 1
        assert result.by_kind["FUNCTION"] == 1
        assert result.is_clean

    def test_scan_by_namespace(self, sqlite_backend):
        _setup_registry_entry(sqlite_backend, name="a", namespace="app1")
        _setup_registry_entry(sqlite_backend, name="b", namespace="app2")
        _setup_registry_entry(sqlite_backend, name="c", namespace="app1")

        intro = RegistryIntrospector(sqlite_backend)
        result = intro.scan()

        assert result.by_namespace["app1"] == 2
        assert result.by_namespace["app2"] == 1

    def test_scan_by_lifecycle(self, sqlite_backend):
        eid = _setup_registry_entry(sqlite_backend, name="active")
        _setup_registry_entry(sqlite_backend, name="other")

        # Archive one
        sqlite_backend.execute(
            "UPDATE registry_entries SET lifecycle = 'ARCHIVED' WHERE id = ?",
            (eid,),
        )

        intro = RegistryIntrospector(sqlite_backend)
        result = intro.scan()

        assert result.active_entries == 1
        assert result.archived_entries == 1

    def test_scan_detects_orphaned_principals(self, sqlite_backend):
        # Create principal with a registry entry, then delete the entry
        reg_id = _setup_registry_entry(sqlite_backend, name="soon_deleted")
        pid = _setup_principal(sqlite_backend, registry_entry_id=reg_id)

        # Disable FK to delete the registry entry and orphan the principal
        sqlite_backend.execute("PRAGMA foreign_keys = OFF", ())
        sqlite_backend.execute("DELETE FROM registry_entries WHERE id = ?", (reg_id,))
        sqlite_backend.execute("PRAGMA foreign_keys = ON", ())

        intro = RegistryIntrospector(sqlite_backend)
        result = intro.scan()

        assert not result.is_clean
        assert pid in result.orphaned_entries

    def test_scan_no_orphans(self, sqlite_backend):
        reg_id = _setup_registry_entry(sqlite_backend, name="valid")
        _setup_principal(sqlite_backend, registry_entry_id=reg_id)

        intro = RegistryIntrospector(sqlite_backend)
        result = intro.scan()

        assert result.is_clean
        assert len(result.orphaned_entries) == 0

    def test_scan_no_duplicate_urns(self, sqlite_backend):
        """With unique constraint enforced, no duplicates should exist."""
        _setup_registry_entry(sqlite_backend, name="unique_a")
        _setup_registry_entry(sqlite_backend, name="unique_b")

        intro = RegistryIntrospector(sqlite_backend)
        result = intro.scan()

        assert len(result.duplicate_urns) == 0

    def test_validate_lifecycle_consistency(self, sqlite_backend):
        _setup_registry_entry(sqlite_backend, name="valid")

        intro = RegistryIntrospector(sqlite_backend)
        invalid = intro.validate_lifecycle_consistency()

        assert len(invalid) == 0

    def test_validate_lifecycle_detects_invalid(self, sqlite_backend):
        eid = _setup_registry_entry(sqlite_backend, name="bad")
        sqlite_backend.execute(
            "UPDATE registry_entries SET lifecycle = 'INVALID_STATE' WHERE id = ?",
            (eid,),
        )

        intro = RegistryIntrospector(sqlite_backend)
        invalid = intro.validate_lifecycle_consistency()

        assert len(invalid) == 1
        assert invalid[0]["lifecycle"] == "INVALID_STATE"
