"""Importable pytest fixtures for downstream pyscoped users.

Register these fixtures in your ``conftest.py``::

    from scoped.testing.fixtures import (
        scoped_backend,
        scoped_services,
        alice,
        bob,
        sample_object,
        sample_scope,
    )

Each fixture is a standalone ``@pytest.fixture`` that participates in
pytest's standard dependency injection.
"""

from __future__ import annotations

import pytest

from scoped.manifest._services import ScopedServices, build_services
from scoped.storage.sqlite import SQLiteBackend
from scoped.tenancy.models import ScopeRole


@pytest.fixture
def scoped_backend():
    """In-memory SQLite backend, initialized with the full schema."""
    backend = SQLiteBackend(":memory:")
    backend.initialize()
    yield backend
    backend.close()


@pytest.fixture
def scoped_services(scoped_backend):
    """Fully-wired ``ScopedServices`` with all 16 layers ready to use."""
    return build_services(scoped_backend)


@pytest.fixture
def alice(scoped_services):
    """A pre-built test principal named Alice."""
    return scoped_services.principals.create_principal(
        kind="user",
        display_name="Alice",
        principal_id="alice",
    )


@pytest.fixture
def bob(scoped_services):
    """A pre-built test principal named Bob."""
    return scoped_services.principals.create_principal(
        kind="user",
        display_name="Bob",
        principal_id="bob",
    )


@pytest.fixture
def sample_object(scoped_services):
    """Factory fixture: call ``sample_object(owner, type, data)`` to create objects."""

    def _create(owner, object_type="Document", data=None):
        return scoped_services.manager.create(
            object_type=object_type,
            owner_id=owner.id,
            data=data or {"title": "Test Document"},
        )

    return _create


@pytest.fixture
def sample_scope(scoped_services):
    """Factory fixture: call ``sample_scope(owner, name, members)`` to create scopes."""

    def _create(owner, name=None, members=None):
        scope = scoped_services.scopes.create_scope(
            name=name or "test-scope",
            owner_id=owner.id,
        )
        for member in (members or []):
            scoped_services.scopes.add_member(
                scope_id=scope.id,
                principal_id=member.id,
                role=ScopeRole.EDITOR,
                granted_by=owner.id,
            )
        return scope

    return _create
