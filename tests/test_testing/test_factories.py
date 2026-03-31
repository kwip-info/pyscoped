"""Tests for ScopedFactory."""

from __future__ import annotations

import pytest

from scoped.manifest._services import build_services
from scoped.storage.sqlite import SQLiteBackend
from scoped.testing.factories import ScopedFactory


@pytest.fixture
def factory():
    backend = SQLiteBackend(":memory:")
    backend.initialize()
    services = build_services(backend)
    return ScopedFactory(services)


class TestScopedFactory:
    def test_principal(self, factory):
        p = factory.principal("Alice")
        assert p.display_name == "Alice"
        assert p.kind == "user"

    def test_principal_auto_name(self, factory):
        p1 = factory.principal()
        p2 = factory.principal()
        assert p1.display_name != p2.display_name

    def test_object(self, factory):
        owner = factory.principal("Owner")
        obj, ver = factory.object(owner)
        assert obj.object_type == "Document"
        assert ver.version == 1

    def test_object_custom_type_and_data(self, factory):
        owner = factory.principal("Owner")
        obj, _ = factory.object(owner, object_type="Invoice", data={"amount": 100})
        assert obj.object_type == "Invoice"

    def test_scope(self, factory):
        owner = factory.principal("Owner")
        scope = factory.scope(owner, name="team-alpha")
        assert scope.name == "team-alpha"

    def test_scope_with_members(self, factory):
        owner = factory.principal("Owner")
        member = factory.principal("Member")
        scope = factory.scope(owner, members=[member])
        assert scope is not None

    def test_secret(self, factory):
        owner = factory.principal("Owner")
        secret, version = factory.secret(owner, name="api-key", value="sk-123")
        assert secret.name == "api-key"
        assert version.version == 1

    def test_project(self, factory):
        owner = factory.principal("Owner")
        obj, _ = factory.object(owner)
        scope = factory.scope(owner)
        # Should not raise
        factory.project(obj, scope, projected_by=owner)
