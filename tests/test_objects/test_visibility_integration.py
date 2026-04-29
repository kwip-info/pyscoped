"""L3↔L4 visibility integration tests.

Before v1.4.1, ``ScopedManager.get()`` and ``list_objects()`` were
owner-only — projection-based visibility (the documented core invariant)
was only honored by the ``VisibilityEngine``, not the public API. This
test module pins the corrected behavior:

* Scope members see projected objects via ``client.objects.get()``.
* ``client.objects.list()`` returns owned + projected.
* Hierarchy inheritance: members of a child scope see objects projected
  into ancestor scopes.
* Without a visibility engine wired, the manager keeps the historical
  owner-only behavior (backward compatibility for direct callers).
"""

from __future__ import annotations

import warnings

import pytest

import scoped
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.tenancy.engine import VisibilityEngine
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.tenancy.projection import ProjectionManager

warnings.filterwarnings("ignore")


@pytest.fixture
def client():
    return scoped.init()


# ----- Through the public client API ------------------------------------

class TestPublicAPI:

    def test_member_sees_projected_object_via_get(self, client):
        alice = client.principals.create("Alice")
        bob = client.principals.create("Bob")
        with client.as_principal(alice):
            doc, _ = client.objects.create("invoice", data={"amount": 100})
            scope = client.scopes.create("Finance")
            client.scopes.add_member(scope, bob, role="editor")
            client.scopes.project(doc, scope)

        with client.as_principal(bob):
            assert client.objects.get(doc.id) is not None

    def test_member_sees_projected_object_via_list(self, client):
        alice = client.principals.create("Alice")
        bob = client.principals.create("Bob")
        with client.as_principal(alice):
            owned, _ = client.objects.create("memo", data={"text": "x"})
            shared, _ = client.objects.create("invoice", data={"amount": 1})
            scope = client.scopes.create("S")
            client.scopes.add_member(scope, bob, role="editor")
            client.scopes.project(shared, scope)

        with client.as_principal(bob):
            invoices = client.objects.list(object_type="invoice")
            assert any(o.id == shared.id for o in invoices)
            # Bob does NOT see Alice's owned-only memo
            memos = client.objects.list(object_type="memo")
            assert all(o.id != owned.id for o in memos)

    def test_non_member_does_not_see_projected_object(self, client):
        alice = client.principals.create("Alice")
        bob = client.principals.create("Bob")  # not added to any scope
        with client.as_principal(alice):
            doc, _ = client.objects.create("invoice", data={"a": 1})
            scope = client.scopes.create("S")
            client.scopes.project(doc, scope)

        with client.as_principal(bob):
            assert client.objects.get(doc.id) is None

    def test_owner_still_sees_own_objects(self, client):
        alice = client.principals.create("Alice")
        with client.as_principal(alice):
            doc, _ = client.objects.create("memo", data={"x": 1})
            assert client.objects.get(doc.id).id == doc.id
            assert any(o.id == doc.id for o in client.objects.list())


# ----- Hierarchy inheritance --------------------------------------------

class TestHierarchyInheritance:

    def test_child_member_sees_object_projected_to_parent(self, client):
        alice = client.principals.create("Alice")
        bob = client.principals.create("Bob")
        with client.as_principal(alice):
            org = client.scopes.create("Org")
            team = client.scopes.create("Team", parent_scope_id=org.id)
            client.scopes.add_member(team, bob, role="editor")

            doc, _ = client.objects.create("policy", data={"v": 1})
            client.scopes.project(doc, org)  # projected into the parent

        # Bob is a member of `team` (child of `org`); via hierarchy he sees `doc`.
        with client.as_principal(bob):
            assert client.objects.get(doc.id) is not None
            assert any(o.id == doc.id for o in client.objects.list())

    def test_visible_object_ids_includes_hierarchy(self, client):
        alice = client.principals.create("Alice")
        bob = client.principals.create("Bob")
        with client.as_principal(alice):
            org = client.scopes.create("Org")
            team = client.scopes.create("Team", parent_scope_id=org.id)
            client.scopes.add_member(team, bob, role="editor")
            doc, _ = client.objects.create("doc", data={"x": 1})
            client.scopes.project(doc, org)

        ids = client.services.visibility_engine.visible_object_ids(bob.id)
        assert doc.id in ids


# ----- Promoted objects flow into client.objects.get/list --------------

class TestPromotionVisibilityFlow:
    """The original motivating scenario for v1.4.1."""

    def test_promoted_object_visible_to_scope_member_via_objects_namespace(
        self, client,
    ):
        alice = client.principals.create("Alice")
        bob = client.principals.create("Bob")
        with client.as_principal(alice):
            env = client.environments.spawn("review")
            client.environments.activate(env)
            doc, _ = client.objects.create("invoice", data={"amount": 500})
            client.environments.add_object(env, doc)

            scope = client.scopes.create("Finance")
            client.scopes.add_member(scope, bob, role="editor")
            client.environments.complete(env)
            client.environments.promote(env, target_scope_id=scope)

        with client.as_principal(bob):
            assert client.objects.get(doc.id) is not None
            assert any(o.id == doc.id for o in client.objects.list(
                object_type="invoice",
            ))


# ----- Backward compat: bare ScopedManager stays owner-only ------------

class TestBackwardCompatNoVisibilityEngine:

    def test_bare_manager_get_owner_only(self, sqlite_backend, registry):
        """Constructing ScopedManager without visibility_engine preserves
        the legacy owner-only behavior."""
        store = PrincipalStore(sqlite_backend)
        alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
        bob = store.create_principal(kind="user", display_name="Bob", principal_id="bob")
        mgr = ScopedManager(sqlite_backend)  # no visibility_engine
        proj = ProjectionManager(sqlite_backend)
        lc = ScopeLifecycle(sqlite_backend)

        obj, _ = mgr.create(object_type="x", owner_id=alice.id, data={})
        scope = lc.create_scope(name="S", owner_id=alice.id)
        lc.add_member(scope.id, principal_id=bob.id, granted_by=alice.id)
        proj.project(scope_id=scope.id, object_id=obj.id, projected_by=alice.id)

        # Without a visibility engine, the manager remains owner-only.
        assert mgr.get(obj.id, principal_id=bob.id) is None

    def test_wired_manager_honors_projection(
        self, sqlite_backend, registry,
    ):
        store = PrincipalStore(sqlite_backend)
        alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
        bob = store.create_principal(kind="user", display_name="Bob", principal_id="bob")
        ve = VisibilityEngine(sqlite_backend)
        mgr = ScopedManager(sqlite_backend, visibility_engine=ve)
        proj = ProjectionManager(sqlite_backend)
        lc = ScopeLifecycle(sqlite_backend)

        obj, _ = mgr.create(object_type="x", owner_id=alice.id, data={})
        scope = lc.create_scope(name="S", owner_id=alice.id)
        lc.add_member(scope.id, principal_id=bob.id, granted_by=alice.id)
        proj.project(scope_id=scope.id, object_id=obj.id, projected_by=alice.id)

        assert mgr.get(obj.id, principal_id=bob.id) is not None
