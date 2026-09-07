"""Tests for PrincipalResolver — graph walking."""

import pytest

from scoped.identity.principal import PrincipalStore
from scoped.identity.resolver import PrincipalResolver


class TestPrincipalResolver:
    """Tests for ancestor/descendant/path resolution."""

    @pytest.fixture
    def store(self, sqlite_backend, registry):
        return PrincipalStore(sqlite_backend)

    @pytest.fixture
    def resolver(self, store):
        return PrincipalResolver(store)

    @pytest.fixture
    def org_tree(self, store, registry):
        """
        Build:  enterprise -> org -> team -> user
        All via 'member_of' relationships.
        """
        enterprise = store.create_principal(kind="enterprise", display_name="Enterprise", registry=registry)
        org = store.create_principal(kind="org", display_name="Acme", registry=registry)
        team = store.create_principal(kind="team", display_name="Engineering", registry=registry)
        user = store.create_principal(kind="user", display_name="Alice", registry=registry)

        store.add_relationship(parent_id=enterprise.id, child_id=org.id, relationship="member_of")
        store.add_relationship(parent_id=org.id, child_id=team.id, relationship="member_of")
        store.add_relationship(parent_id=team.id, child_id=user.id, relationship="member_of")

        return {"enterprise": enterprise, "org": org, "team": team, "user": user}

    def test_ancestors(self, resolver, org_tree):
        ancestors = resolver.ancestors(org_tree["user"].id)
        ancestor_ids = {a.id for a in ancestors}
        assert org_tree["team"].id in ancestor_ids
        assert org_tree["org"].id in ancestor_ids
        assert org_tree["enterprise"].id in ancestor_ids

    def test_ancestors_with_max_depth(self, resolver, org_tree):
        ancestors = resolver.ancestors(org_tree["user"].id, max_depth=1)
        ancestor_ids = {a.id for a in ancestors}
        assert org_tree["team"].id in ancestor_ids
        assert org_tree["enterprise"].id not in ancestor_ids

    def test_descendants(self, resolver, org_tree):
        descendants = resolver.descendants(org_tree["enterprise"].id)
        desc_ids = {d.id for d in descendants}
        assert org_tree["org"].id in desc_ids
        assert org_tree["team"].id in desc_ids
        assert org_tree["user"].id in desc_ids

    def test_descendants_with_max_depth(self, resolver, org_tree):
        descendants = resolver.descendants(org_tree["enterprise"].id, max_depth=1)
        desc_ids = {d.id for d in descendants}
        assert org_tree["org"].id in desc_ids
        assert org_tree["user"].id not in desc_ids

    def test_parents(self, resolver, org_tree):
        parents = resolver.parents(org_tree["team"].id)
        assert len(parents) == 1
        assert parents[0].id == org_tree["org"].id

    def test_children(self, resolver, org_tree):
        children = resolver.children(org_tree["org"].id)
        assert len(children) == 1
        assert children[0].id == org_tree["team"].id

    def test_find_path(self, resolver, org_tree):
        path = resolver.find_path(org_tree["user"].id, org_tree["enterprise"].id)
        assert path is not None
        assert path.length == 3
        assert org_tree["user"].id in path.principals
        assert org_tree["enterprise"].id in path.principals

    def test_find_path_no_connection(self, resolver, store, registry):
        isolated = store.create_principal(kind="user", display_name="Lone", registry=registry)
        other = store.create_principal(kind="user", display_name="Other", registry=registry)
        path = resolver.find_path(isolated.id, other.id)
        assert path is None

    def test_is_related(self, resolver, org_tree):
        assert resolver.is_related(org_tree["user"].id, org_tree["enterprise"].id)
        assert resolver.is_related(org_tree["enterprise"].id, org_tree["user"].id)

    def test_is_not_related(self, resolver, store, registry):
        a = store.create_principal(kind="user", display_name="A", registry=registry)
        b = store.create_principal(kind="user", display_name="B", registry=registry)
        assert not resolver.is_related(a.id, b.id)

    def test_all_related_ids(self, resolver, org_tree):
        related = resolver.all_related_ids(org_tree["team"].id)
        assert org_tree["team"].id in related
        assert org_tree["org"].id in related
        assert org_tree["enterprise"].id in related
        assert org_tree["user"].id in related

    def test_relationship_filter(self, resolver, store, registry):
        """Only follow edges with the specified relationship label."""
        org = store.create_principal(kind="org", display_name="Org", registry=registry)
        user = store.create_principal(kind="user", display_name="User", registry=registry)
        bot = store.create_principal(kind="bot", display_name="Bot", registry=registry)

        store.add_relationship(parent_id=org.id, child_id=user.id, relationship="member_of")
        store.add_relationship(parent_id=org.id, child_id=bot.id, relationship="owns")

        members = resolver.descendants(org.id, relationship="member_of")
        assert len(members) == 1
        assert members[0].id == user.id

        owned = resolver.descendants(org.id, relationship="owns")
        assert len(owned) == 1
        assert owned[0].id == bot.id

    def test_multi_parent_graph(self, resolver, store, registry):
        """A principal can have multiple parents (e.g., user in two teams)."""
        team_a = store.create_principal(kind="team", display_name="Team A", registry=registry)
        team_b = store.create_principal(kind="team", display_name="Team B", registry=registry)
        user = store.create_principal(kind="user", display_name="Multi", registry=registry)

        store.add_relationship(parent_id=team_a.id, child_id=user.id, relationship="member_of")
        store.add_relationship(parent_id=team_b.id, child_id=user.id, relationship="member_of")

        parents = resolver.parents(user.id)
        parent_ids = {p.id for p in parents}
        assert team_a.id in parent_ids
        assert team_b.id in parent_ids

    def test_empty_graph(self, resolver, store, registry):
        lone = store.create_principal(kind="user", display_name="Lone", registry=registry)
        assert resolver.ancestors(lone.id) == []
        assert resolver.descendants(lone.id) == []
        assert resolver.parents(lone.id) == []
        assert resolver.children(lone.id) == []
