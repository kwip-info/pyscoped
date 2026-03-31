"""Tests for built-in rule factories."""

import pytest

from scoped.identity.principal import PrincipalStore
from scoped.rules.builtins import (
    allow_crud_in_scope,
    deny_action_in_scope,
    deny_sharing_outside_scope,
    restrict_to_principal_kind,
)
from scoped.rules.engine import RuleEngine, RuleStore
from scoped.rules.models import RuleEffect, RuleType


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    admin = store.create_principal(kind="user", display_name="Admin", principal_id="admin")
    return admin


@pytest.fixture
def store(sqlite_backend):
    return RuleStore(sqlite_backend)


@pytest.fixture
def engine(sqlite_backend):
    return RuleEngine(sqlite_backend)


class TestAllowCrudInScope:

    def test_allows_crud(self, store, engine, principals):
        admin = principals
        allow_crud_in_scope(store, scope_id="s1", created_by=admin.id)

        assert engine.evaluate(action="create", scope_id="s1").allowed
        assert engine.evaluate(action="read", scope_id="s1").allowed
        assert engine.evaluate(action="update", scope_id="s1").allowed
        assert engine.evaluate(action="delete", scope_id="s1").allowed

    def test_doesnt_allow_other_actions(self, store, engine, principals):
        admin = principals
        allow_crud_in_scope(store, scope_id="s1", created_by=admin.id)
        # "share" is not in the CRUD list
        assert not engine.evaluate(action="share", scope_id="s1").allowed

    def test_scoped_to_object_type(self, store, engine, principals):
        admin = principals
        allow_crud_in_scope(store, scope_id="s1", created_by=admin.id, object_type="Doc")

        assert engine.evaluate(action="read", scope_id="s1", object_type="Doc").allowed
        assert not engine.evaluate(action="read", scope_id="s1", object_type="Task").allowed


class TestDenyActionInScope:

    def test_denies_action(self, store, engine, principals):
        admin = principals
        # First allow CRUD, then deny delete
        allow_crud_in_scope(store, scope_id="s1", created_by=admin.id)
        deny_action_in_scope(store, scope_id="s1", action="delete", created_by=admin.id)

        assert engine.evaluate(action="read", scope_id="s1").allowed
        assert not engine.evaluate(action="delete", scope_id="s1").allowed


class TestDenySharingOutsideScope:

    def test_denies_projection(self, store, engine, principals):
        admin = principals
        deny_sharing_outside_scope(store, scope_id="s1", created_by=admin.id)

        result = engine.evaluate(action="projection", scope_id="s1")
        assert not result.allowed


class TestRestrictToPrincipalKind:

    def test_denies_by_kind(self, store, engine, principals):
        admin = principals
        allow_crud_in_scope(store, scope_id="s1", created_by=admin.id)
        restrict_to_principal_kind(
            store, principal_kind="bot", action="delete",
            scope_id="s1", created_by=admin.id,
        )

        # Bot trying to delete → denied
        assert not engine.evaluate(
            action="delete", scope_id="s1", principal_kind="bot",
        ).allowed
        # User trying to delete → allowed (restriction doesn't match "user" kind)
        assert engine.evaluate(
            action="delete", scope_id="s1", principal_kind="user",
        ).allowed
