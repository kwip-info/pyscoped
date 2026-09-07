"""Tests for namespace API completeness (P2 item 4B).

Covers principal archive/relationships, scope hierarchy, pagination
(limit/offset), and audit export/count.
"""

import json

import pytest

from scoped.audit.query import AuditQuery
from scoped.audit.writer import AuditWriter
from scoped.identity.principal import PrincipalStore
from scoped.manifest._services import ScopedServices, build_services
from scoped.objects.manager import ScopedManager
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.tenancy.models import ScopeRole
from scoped.tenancy.projection import ProjectionManager
from scoped.types import ActionType, Lifecycle

from scoped._namespaces.audit import AuditNamespace
from scoped._namespaces.principals import PrincipalsNamespace
from scoped._namespaces.scopes import ScopesNamespace


@pytest.fixture
def services(sqlite_backend):
    return build_services(sqlite_backend)


@pytest.fixture
def principals_ns(services):
    return PrincipalsNamespace(services)


@pytest.fixture
def scopes_ns(services):
    return ScopesNamespace(services)


@pytest.fixture
def audit_ns(services):
    return AuditNamespace(services)


@pytest.fixture
def principal_store(sqlite_backend, registry):
    return PrincipalStore(sqlite_backend)


# ---- Principals: archive ---------------------------------------------------


class TestPrincipalsArchive:

    def test_principals_archive(self, principal_store, principals_ns, registry):
        """Archive a principal, verify lifecycle becomes ARCHIVED."""
        alice = principal_store.create_principal(
            kind="user", display_name="Alice", principal_id="alice",
        )
        assert alice.lifecycle == Lifecycle.ACTIVE

        archived = principals_ns.archive(alice)
        assert archived.lifecycle == Lifecycle.ARCHIVED


# ---- Principals: list with limit -------------------------------------------


class TestPrincipalsListWithLimit:

    def test_principals_list_with_limit(self, principal_store, principals_ns, registry):
        """Create 5 principals, list with limit=3, verify only 3 returned."""
        for i in range(5):
            principal_store.create_principal(
                kind="user",
                display_name=f"User{i}",
                principal_id=f"user-{i}",
            )

        result = principals_ns.list(limit=3)
        assert len(result) == 3


# ---- Principals: relationships ---------------------------------------------


class TestPrincipalsRelationships:

    def test_principals_add_relationship(self, principal_store, principals_ns, registry):
        """Add a relationship between two principals, verify via relationships()."""
        org = principal_store.create_principal(
            kind="org", display_name="Acme", principal_id="org-acme",
        )
        alice = principal_store.create_principal(
            kind="user", display_name="Alice", principal_id="alice",
        )

        rel = principals_ns.add_relationship(
            org, alice, relationship="member_of", created_by=alice.id,
        )
        assert rel.parent_id == org.id
        assert rel.child_id == alice.id
        assert rel.relationship == "member_of"

        rels = principals_ns.relationships(alice)
        assert len(rels) >= 1
        assert any(r.parent_id == org.id for r in rels)


# ---- Scopes: children ------------------------------------------------------


class TestScopeChildren:

    def test_scope_children(self, principal_store, scopes_ns, services, registry):
        """Create parent + 2 children, verify children() returns 2."""
        alice = principal_store.create_principal(
            kind="user", display_name="Alice", principal_id="alice",
        )
        parent = services.scopes.create_scope(name="Org", owner_id=alice.id)
        services.scopes.create_scope(
            name="Team A", owner_id=alice.id, parent_scope_id=parent.id,
        )
        services.scopes.create_scope(
            name="Team B", owner_id=alice.id, parent_scope_id=parent.id,
        )

        kids = scopes_ns.children(parent)
        assert len(kids) == 2
        names = {k.name for k in kids}
        assert names == {"Team A", "Team B"}


# ---- Scopes: ancestors -----------------------------------------------------


class TestScopeAncestors:

    def test_scope_ancestors(self, principal_store, scopes_ns, services, registry):
        """Create root -> parent -> child, verify ancestors(child) = [parent, root]."""
        alice = principal_store.create_principal(
            kind="user", display_name="Alice", principal_id="alice",
        )
        root = services.scopes.create_scope(name="Root", owner_id=alice.id)
        parent = services.scopes.create_scope(
            name="Parent", owner_id=alice.id, parent_scope_id=root.id,
        )
        child = services.scopes.create_scope(
            name="Child", owner_id=alice.id, parent_scope_id=parent.id,
        )

        ancestors = scopes_ns.ancestors(child)
        assert len(ancestors) == 2
        assert ancestors[0].id == parent.id
        assert ancestors[1].id == root.id


# ---- Scopes: path ----------------------------------------------------------


class TestScopePath:

    def test_scope_path(self, principal_store, scopes_ns, services, registry):
        """Verify path(child) returns [root, parent, child]."""
        alice = principal_store.create_principal(
            kind="user", display_name="Alice", principal_id="alice",
        )
        root = services.scopes.create_scope(name="Root", owner_id=alice.id)
        parent = services.scopes.create_scope(
            name="Parent", owner_id=alice.id, parent_scope_id=root.id,
        )
        child = services.scopes.create_scope(
            name="Child", owner_id=alice.id, parent_scope_id=parent.id,
        )

        p = scopes_ns.path(child)
        assert len(p) == 3
        assert p[0].id == root.id
        assert p[1].id == parent.id
        assert p[2].id == child.id


# ---- Scopes: descendants ---------------------------------------------------


class TestScopeDescendants:

    def test_scope_descendants(self, principal_store, scopes_ns, services, registry):
        """Create a tree and verify descendants returns all."""
        alice = principal_store.create_principal(
            kind="user", display_name="Alice", principal_id="alice",
        )
        root = services.scopes.create_scope(name="Root", owner_id=alice.id)
        child_a = services.scopes.create_scope(
            name="A", owner_id=alice.id, parent_scope_id=root.id,
        )
        child_b = services.scopes.create_scope(
            name="B", owner_id=alice.id, parent_scope_id=root.id,
        )
        grandchild = services.scopes.create_scope(
            name="A1", owner_id=alice.id, parent_scope_id=child_a.id,
        )

        desc = scopes_ns.descendants(root)
        desc_ids = {d.id for d in desc}
        assert child_a.id in desc_ids
        assert child_b.id in desc_ids
        assert grandchild.id in desc_ids
        assert root.id not in desc_ids


# ---- Members: limit --------------------------------------------------------


class TestMembersWithLimit:

    def test_members_with_limit(self, principal_store, scopes_ns, services, registry):
        """Add 3 members, list with limit=2, verify only 2 returned."""
        alice = principal_store.create_principal(
            kind="user", display_name="Alice", principal_id="alice",
        )
        bob = principal_store.create_principal(
            kind="user", display_name="Bob", principal_id="bob",
        )
        carol = principal_store.create_principal(
            kind="user", display_name="Carol", principal_id="carol",
        )

        scope = services.scopes.create_scope(name="Team", owner_id=alice.id)
        # alice is auto-added as owner, add bob and carol
        services.scopes.add_member(
            scope.id, principal_id=bob.id, role=ScopeRole.VIEWER, granted_by=alice.id,
        )
        services.scopes.add_member(
            scope.id, principal_id=carol.id, role=ScopeRole.VIEWER, granted_by=alice.id,
        )

        # 3 total members (alice, bob, carol); limit=2
        result = scopes_ns.members(scope, limit=2)
        assert len(result) == 2


# ---- Audit: count ----------------------------------------------------------


class TestAuditCount:

    def test_audit_count(self, sqlite_backend, audit_ns):
        """Record events, verify count matches."""
        writer = AuditWriter(sqlite_backend)
        writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="Doc", target_id="d1",
        )
        writer.record(
            actor_id="alice", action=ActionType.UPDATE,
            target_type="Doc", target_id="d1",
        )
        writer.record(
            actor_id="bob", action=ActionType.CREATE,
            target_type="Doc", target_id="d2",
        )

        total = audit_ns.count()
        assert total == 3

        alice_creates = audit_ns.count(
            actor_id="alice", action=ActionType.CREATE,
        )
        assert alice_creates == 1


# ---- Audit: export JSON ----------------------------------------------------


class TestAuditExportJson:

    def test_audit_export_json(self, sqlite_backend, audit_ns):
        """Export as JSON, verify valid JSON with correct structure."""
        writer = AuditWriter(sqlite_backend)
        writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="Doc", target_id="d1",
        )
        writer.record(
            actor_id="bob", action=ActionType.UPDATE,
            target_type="Doc", target_id="d1",
        )

        output = audit_ns.export(format="json")
        data = json.loads(output)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["actor_id"] == "alice"
        assert "timestamp" in data[0]
        assert "action" in data[0]


# ---- Audit: export CSV ----------------------------------------------------


class TestAuditExportCsv:

    def test_audit_export_csv(self, sqlite_backend, audit_ns):
        """Export as CSV, verify has header + rows."""
        writer = AuditWriter(sqlite_backend)
        writer.record(
            actor_id="alice", action=ActionType.CREATE,
            target_type="Doc", target_id="d1",
        )
        writer.record(
            actor_id="bob", action=ActionType.READ,
            target_type="Doc", target_id="d1",
        )

        output = audit_ns.export(format="csv")
        lines = output.strip().split("\n")
        # Header + 2 data rows
        assert len(lines) == 3
        header = lines[0]
        assert "id" in header
        assert "actor_id" in header
        assert "action" in header
        assert "timestamp" in header
