"""Tests for SDK namespace classes — context-aware defaults, object/ID acceptance."""

from __future__ import annotations

import pytest

from scoped.client import ScopedClient


@pytest.fixture
def client():
    c = ScopedClient()
    yield c
    c.close()


@pytest.fixture
def alice(client):
    return client.principals.create("Alice")


@pytest.fixture
def bob(client):
    return client.principals.create("Bob")


class TestPrincipalsNamespace:
    def test_create(self, client):
        p = client.principals.create("Test User")
        assert p.display_name == "Test User"
        assert p.kind == "user"

    def test_create_custom_kind(self, client):
        p = client.principals.create("CI Bot", kind="service")
        assert p.kind == "service"

    def test_get(self, client, alice):
        found = client.principals.get(alice.id)
        assert found.id == alice.id

    def test_find_returns_none(self, client):
        assert client.principals.find("nonexistent") is None

    def test_list(self, client, alice, bob):
        all_p = client.principals.list()
        ids = {p.id for p in all_p}
        assert alice.id in ids
        assert bob.id in ids

    def test_list_by_kind(self, client):
        client.principals.create("User1", kind="user")
        client.principals.create("Bot1", kind="service")
        users = client.principals.list(kind="user")
        assert all(p.kind == "user" for p in users)


class TestObjectsNamespace:
    def test_create_with_context(self, client, alice):
        with client.as_principal(alice):
            doc, v1 = client.objects.create("invoice", data={"amount": 100})
        assert doc.object_type == "invoice"
        assert doc.owner_id == alice.id
        assert v1.version == 1

    def test_create_explicit_owner(self, client, alice):
        doc, _ = client.objects.create(
            "note", data={"text": "hi"}, owner_id=alice.id
        )
        assert doc.owner_id == alice.id

    def test_create_without_context_raises(self, client):
        with pytest.raises(RuntimeError, match="No principal"):
            client.objects.create("doc", data={"x": 1})

    def test_get_with_context(self, client, alice):
        with client.as_principal(alice):
            doc, _ = client.objects.create("doc", data={"x": 1})
            found = client.objects.get(doc.id)
        assert found is not None
        assert found.id == doc.id

    def test_isolation(self, client, alice, bob):
        with client.as_principal(alice):
            doc, _ = client.objects.create("doc", data={"secret": True})
        with client.as_principal(bob):
            assert client.objects.get(doc.id) is None

    def test_update(self, client, alice):
        with client.as_principal(alice):
            doc, v1 = client.objects.create("doc", data={"v": 1})
            doc2, v2 = client.objects.update(doc.id, data={"v": 2})
        assert v2.version == 2

    def test_delete(self, client, alice):
        with client.as_principal(alice):
            doc, _ = client.objects.create("doc", data={"x": 1})
            tombstone = client.objects.delete(doc.id, reason="test")
        assert tombstone is not None

    def test_list(self, client, alice):
        with client.as_principal(alice):
            client.objects.create("a", data={"x": 1})
            client.objects.create("b", data={"x": 2})
            objs = client.objects.list()
        assert len(objs) >= 2

    def test_versions(self, client, alice):
        with client.as_principal(alice):
            doc, _ = client.objects.create("doc", data={"v": 1})
            client.objects.update(doc.id, data={"v": 2})
            client.objects.update(doc.id, data={"v": 3})
            vers = client.objects.versions(doc.id)
        assert len(vers) == 3


class TestScopesNamespace:
    def test_create(self, client, alice):
        with client.as_principal(alice):
            scope = client.scopes.create("Engineering")
        assert scope.name == "Engineering"

    def test_add_member_with_objects(self, client, alice, bob):
        """Accept Principal objects directly, not just IDs."""
        with client.as_principal(alice):
            scope = client.scopes.create("Team")
            membership = client.scopes.add_member(scope, bob, role="editor")
        assert membership is not None

    def test_add_member_with_string_ids(self, client, alice, bob):
        """Accept string IDs too."""
        with client.as_principal(alice):
            scope = client.scopes.create("Team")
            membership = client.scopes.add_member(scope.id, bob.id, role="viewer")
        assert membership is not None

    def test_project_and_visibility(self, client, alice, bob):
        with client.as_principal(alice):
            doc, _ = client.objects.create("doc", data={"shared": True})
            scope = client.scopes.create("Shared")
            client.scopes.add_member(scope, bob, role="viewer")
            client.scopes.project(doc, scope)

    def test_unproject(self, client, alice):
        with client.as_principal(alice):
            doc, _ = client.objects.create("doc", data={"x": 1})
            scope = client.scopes.create("Temp")
            client.scopes.project(doc, scope)
            result = client.scopes.unproject(doc, scope)
        assert result is True

    def test_members(self, client, alice, bob):
        with client.as_principal(alice):
            scope = client.scopes.create("Team")
            client.scopes.add_member(scope, bob, role="editor")
            members = client.scopes.members(scope)
        assert len(members) >= 1

    def test_projections(self, client, alice):
        with client.as_principal(alice):
            doc, _ = client.objects.create("doc", data={"x": 1})
            scope = client.scopes.create("S")
            client.scopes.project(doc, scope)
            projs = client.scopes.projections(scope)
        assert len(projs) == 1

    def test_freeze(self, client, alice):
        with client.as_principal(alice):
            scope = client.scopes.create("Freezable")
            frozen = client.scopes.freeze(scope)
        assert frozen is not None

    def test_archive(self, client, alice):
        with client.as_principal(alice):
            scope = client.scopes.create("Archivable")
            archived = client.scopes.archive(scope)
        assert archived is not None


class TestAuditNamespace:
    def test_for_object(self, client, alice):
        with client.as_principal(alice):
            doc, _ = client.objects.create("doc", data={"x": 1})
            trail = client.audit.for_object(doc.id)
        assert len(trail) >= 1

    def test_for_principal(self, client, alice):
        with client.as_principal(alice):
            client.objects.create("doc", data={"x": 1})
            trail = client.audit.for_principal(alice.id)
        assert len(trail) >= 1

    def test_verify(self, client, alice):
        with client.as_principal(alice):
            client.objects.create("doc", data={"x": 1})
        result = client.audit.verify()
        assert result.valid

    def test_query_kwargs(self, client, alice):
        with client.as_principal(alice):
            client.objects.create("doc", data={"x": 1})
        results = client.audit.query(actor_id=alice.id, limit=5)
        assert len(results) >= 1


class TestSecretsNamespace:
    def test_create(self, client, alice):
        with client.as_principal(alice):
            secret, v1 = client.secrets.create("api-key", "sk-12345")
        assert secret.name == "api-key"
        assert v1.version == 1

    def test_rotate(self, client, alice):
        with client.as_principal(alice):
            secret, _ = client.secrets.create("key", "old-value")
            v2 = client.secrets.rotate(secret.id, new_value="new-value")
        assert v2.version == 2

    def test_grant_and_resolve(self, client, alice, bob):
        with client.as_principal(alice):
            secret, _ = client.secrets.create("key", "secret-value")
            ref = client.secrets.grant_ref(secret.id, bob)

        with client.as_principal(bob):
            value = client.secrets.resolve(ref.ref_token)
        assert value == "secret-value"


class TestToId:
    """Verify that _to_id works with both objects and strings."""

    def test_string_passthrough(self):
        from scoped._namespaces._base import _to_id

        assert _to_id("abc-123") == "abc-123"

    def test_object_with_id(self, client, alice):
        from scoped._namespaces._base import _to_id

        assert _to_id(alice) == alice.id

    def test_invalid_type(self):
        from scoped._namespaces._base import _to_id

        with pytest.raises(TypeError, match="string ID"):
            _to_id(42)
