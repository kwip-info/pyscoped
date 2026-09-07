"""Tests for typed ID system."""

from __future__ import annotations

from scoped.ids import (
    BindingId,
    ConnectorId,
    EntryId,
    JobId,
    MembershipId,
    ObjectId,
    PrincipalId,
    ProjectionId,
    RuleId,
    ScheduleId,
    ScopeId,
    ScopedId,
    SecretId,
    TraceId,
    VersionId,
)


ALL_ID_TYPES = [
    ScopedId, PrincipalId, ObjectId, VersionId, ScopeId, MembershipId,
    ProjectionId, RuleId, BindingId, TraceId, EntryId, SecretId,
    ConnectorId, ScheduleId, JobId,
]


class TestScopedIdBase:
    """Base class behavior."""

    def test_is_str_subclass(self):
        pid = PrincipalId("abc123")
        assert isinstance(pid, str)
        assert isinstance(pid, ScopedId)
        assert isinstance(pid, PrincipalId)

    def test_str_equality(self):
        pid = PrincipalId("abc123")
        assert pid == "abc123"
        assert "abc123" == pid

    def test_generate_returns_typed(self):
        pid = PrincipalId.generate()
        assert isinstance(pid, PrincipalId)
        assert isinstance(pid, str)
        assert len(pid) == 32  # uuid4 hex

    def test_generate_uniqueness(self):
        ids = {PrincipalId.generate() for _ in range(1000)}
        assert len(ids) == 1000

    def test_repr_shows_type(self):
        pid = PrincipalId("abc")
        assert "PrincipalId" in repr(pid)
        assert "abc" in repr(pid)

    def test_hashable(self):
        pid = PrincipalId("abc")
        d = {pid: "value"}
        assert d["abc"] == "value"
        assert d[pid] == "value"

    def test_json_serializable(self):
        import json
        pid = PrincipalId("abc123")
        assert json.dumps(pid) == '"abc123"'

    def test_usable_in_format_string(self):
        pid = PrincipalId("abc123")
        assert f"principal:{pid}" == "principal:abc123"


class TestAllIdTypes:
    """Verify all ID types work correctly."""

    def test_all_types_are_str_subclasses(self):
        for cls in ALL_ID_TYPES:
            instance = cls("test")
            assert isinstance(instance, str), f"{cls.__name__} is not a str"

    def test_all_types_generate(self):
        for cls in ALL_ID_TYPES:
            instance = cls.generate()
            assert isinstance(instance, cls), f"{cls.__name__}.generate() wrong type"
            assert len(instance) == 32, f"{cls.__name__}.generate() wrong length"

    def test_types_are_distinct(self):
        """Different ID types are different Python types."""
        assert PrincipalId is not ObjectId
        assert ObjectId is not ScopeId
        pid = PrincipalId("x")
        oid = ObjectId("x")
        # Values equal (both are "x") but types differ
        assert pid == oid
        assert type(pid) is not type(oid)


class TestBackwardCompatibility:
    """Typed IDs must work everywhere plain str works."""

    def test_passed_as_str_parameter(self):
        def accepts_str(s: str) -> str:
            return s.upper()
        pid = PrincipalId("abc")
        assert accepts_str(pid) == "ABC"

    def test_string_methods(self):
        pid = PrincipalId("abc123")
        assert pid.startswith("abc")
        assert pid.endswith("123")
        assert pid[:3] == "abc"

    def test_constructed_from_existing_str(self):
        raw = "existing_id_from_database"
        pid = PrincipalId(raw)
        assert pid == raw
        assert isinstance(pid, PrincipalId)


class TestReExportsFromTypes:
    """Verify types.py re-exports all ID types."""

    def test_imports_from_types(self):
        from scoped.types import PrincipalId as P, ObjectId as O, ScopeId as S
        assert P is PrincipalId
        assert O is ObjectId
        assert S is ScopeId
