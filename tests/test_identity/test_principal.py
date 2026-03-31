"""Tests for Principal, PrincipalRelationship, and PrincipalStore."""

import pytest

from scoped.exceptions import PrincipalNotFoundError
from scoped.identity.principal import (
    Principal,
    PrincipalRelationship,
    PrincipalStore,
)
from scoped.types import Lifecycle


class TestPrincipal:
    """Unit tests for the Principal dataclass."""

    def test_create_principal(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        p = store.create_principal(
            kind="user",
            display_name="Alice",
            registry=registry,
        )
        assert p.kind == "user"
        assert p.display_name == "Alice"
        assert p.is_active
        assert p.lifecycle == Lifecycle.ACTIVE

    def test_principal_snapshot(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        p = store.create_principal(
            kind="bot",
            display_name="TestBot",
            metadata={"purpose": "testing"},
            registry=registry,
        )
        snap = p.snapshot()
        assert snap["kind"] == "bot"
        assert snap["display_name"] == "TestBot"
        assert snap["metadata"]["purpose"] == "testing"
        assert snap["lifecycle"] == "ACTIVE"

    def test_get_principal(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        created = store.create_principal(kind="user", display_name="Bob", registry=registry)
        fetched = store.get_principal(created.id)
        assert fetched.id == created.id
        assert fetched.kind == "user"
        assert fetched.display_name == "Bob"

    def test_get_principal_not_found(self, sqlite_backend):
        store = PrincipalStore(sqlite_backend)
        with pytest.raises(PrincipalNotFoundError):
            store.get_principal("nonexistent")

    def test_find_principal_returns_none(self, sqlite_backend):
        store = PrincipalStore(sqlite_backend)
        assert store.find_principal("nonexistent") is None

    def test_list_principals_by_kind(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        store.create_principal(kind="user", display_name="U1", registry=registry)
        store.create_principal(kind="user", display_name="U2", registry=registry)
        store.create_principal(kind="bot", display_name="B1", registry=registry)

        users = store.list_principals(kind="user")
        assert len(users) == 2
        bots = store.list_principals(kind="bot")
        assert len(bots) == 1

    def test_list_principals_by_lifecycle(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        p = store.create_principal(kind="user", display_name="X", registry=registry)
        store.update_lifecycle(p.id, Lifecycle.ARCHIVED)

        active = store.list_principals(lifecycle=Lifecycle.ACTIVE)
        archived = store.list_principals(lifecycle=Lifecycle.ARCHIVED)
        assert all(a.lifecycle == Lifecycle.ACTIVE for a in active)
        assert len(archived) == 1
        assert archived[0].id == p.id

    def test_update_lifecycle(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        p = store.create_principal(kind="user", display_name="Z", registry=registry)
        assert p.is_active

        updated = store.update_lifecycle(p.id, Lifecycle.ARCHIVED)
        assert updated.lifecycle == Lifecycle.ARCHIVED
        assert not updated.is_active

        # Verify persistence
        refetched = store.get_principal(p.id)
        assert refetched.lifecycle == Lifecycle.ARCHIVED

    def test_custom_principal_kinds(self, sqlite_backend, registry):
        """Applications can define any kind — the framework doesn't restrict."""
        store = PrincipalStore(sqlite_backend)
        p1 = store.create_principal(kind="service_account", display_name="SA1", registry=registry)
        p2 = store.create_principal(kind="deployment_pipeline", display_name="DP1", registry=registry)
        assert p1.kind == "service_account"
        assert p2.kind == "deployment_pipeline"

    def test_principal_with_metadata(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        p = store.create_principal(
            kind="user",
            display_name="Meta",
            metadata={"email": "meta@example.com", "role": "admin"},
            registry=registry,
        )
        fetched = store.get_principal(p.id)
        assert fetched.metadata.get("email") == "meta@example.com"
        assert fetched.metadata.get("role") == "admin"

    def test_principal_registered_in_registry(self, sqlite_backend, registry):
        """Principals must be registered constructs."""
        store = PrincipalStore(sqlite_backend)
        p = store.create_principal(kind="user", display_name="Reg", registry=registry)
        entry = registry.get(p.registry_entry_id)
        assert entry is not None
        assert entry.kind.name == "PRINCIPAL"

    def test_store_requires_storage_backend(self):
        with pytest.raises(TypeError, match="StorageBackend"):
            PrincipalStore("not_a_backend")


class TestPrincipalRelationship:
    """Tests for relationship CRUD."""

    def test_add_relationship(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        org = store.create_principal(kind="org", display_name="Acme", registry=registry)
        user = store.create_principal(kind="user", display_name="Alice", registry=registry)

        rel = store.add_relationship(
            parent_id=org.id,
            child_id=user.id,
            relationship="member_of",
            created_by="system",
        )
        assert rel.parent_id == org.id
        assert rel.child_id == user.id
        assert rel.relationship == "member_of"

    def test_get_relationships_parent_direction(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        org = store.create_principal(kind="org", display_name="Acme", registry=registry)
        user = store.create_principal(kind="user", display_name="Alice", registry=registry)
        store.add_relationship(parent_id=org.id, child_id=user.id, relationship="member_of")

        # user's parents
        rels = store.get_relationships(user.id, direction="parent")
        assert len(rels) == 1
        assert rels[0].parent_id == org.id

    def test_get_relationships_child_direction(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        org = store.create_principal(kind="org", display_name="Acme", registry=registry)
        user = store.create_principal(kind="user", display_name="Alice", registry=registry)
        store.add_relationship(parent_id=org.id, child_id=user.id, relationship="member_of")

        # org's children
        rels = store.get_relationships(org.id, direction="child")
        assert len(rels) == 1
        assert rels[0].child_id == user.id

    def test_get_relationships_with_filter(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        org = store.create_principal(kind="org", display_name="Acme", registry=registry)
        team = store.create_principal(kind="team", display_name="Engineering", registry=registry)
        user = store.create_principal(kind="user", display_name="Alice", registry=registry)

        store.add_relationship(parent_id=org.id, child_id=user.id, relationship="member_of")
        store.add_relationship(parent_id=team.id, child_id=user.id, relationship="belongs_to")

        # Filter by relationship type
        member_rels = store.get_relationships(user.id, direction="parent", relationship="member_of")
        assert len(member_rels) == 1
        assert member_rels[0].parent_id == org.id

    def test_remove_relationship(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        a = store.create_principal(kind="org", display_name="A", registry=registry)
        b = store.create_principal(kind="user", display_name="B", registry=registry)
        rel = store.add_relationship(parent_id=a.id, child_id=b.id)

        store.remove_relationship(rel.id)
        rels = store.get_relationships(b.id, direction="parent")
        assert len(rels) == 0

    def test_relationship_requires_existing_principals(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        real = store.create_principal(kind="user", display_name="Real", registry=registry)
        with pytest.raises(PrincipalNotFoundError):
            store.add_relationship(parent_id="fake", child_id=real.id)

    def test_relationship_snapshot(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        a = store.create_principal(kind="org", display_name="A", registry=registry)
        b = store.create_principal(kind="user", display_name="B", registry=registry)
        rel = store.add_relationship(
            parent_id=a.id, child_id=b.id, relationship="owns",
            metadata={"note": "test"},
        )
        snap = rel.snapshot()
        assert snap["relationship"] == "owns"
        assert snap["metadata"]["note"] == "test"
