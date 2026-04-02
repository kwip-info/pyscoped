---
title: "Testing Guide"
description: "Write reliable tests for pyscoped-powered applications using the built-in test utilities, fixtures, and patterns."
category: "Guides"
---

# Testing Guide

pyscoped ships with dedicated testing utilities that make it straightforward to
write fast, isolated tests. This guide covers the base test class, data
factories, pytest fixtures, integration patterns for Django and FastAPI, and
recipes for testing rules, audit trails, and more.

## ScopedTestCase

`ScopedTestCase` is a `unittest.TestCase` subclass that sets up and tears down
a fresh in-memory SQLite backend for every test method.

```python
from scoped.testing.base import ScopedTestCase


class TestMyFeature(ScopedTestCase):
    def test_create_scope(self):
        scope = self.client.create_scope(
            name="test-scope",
            owner_id="user-1",
        )
        self.assertEqual(scope["name"], "test-scope")
```

`self.client` is a fully initialised `scoped.Client` backed by an in-memory
SQLite database. `self.storage` gives direct access to the storage backend.

Every test method gets a clean database -- no state leaks between tests.

## ScopedFactory

`ScopedFactory` generates test data with sensible defaults so your tests can
focus on the behaviour under test rather than boilerplate setup.

```python
from scoped.testing.base import ScopedTestCase
from scoped.testing.factory import ScopedFactory


class TestWithFactory(ScopedTestCase):
    def setUp(self):
        super().setUp()
        self.factory = ScopedFactory(self.client)

    def test_membership(self):
        scope = self.factory.scope()
        user = self.factory.principal()
        self.factory.membership(scope_id=scope["id"], principal_id=user[0])

        members = self.client.list_members(scope_id=scope["id"])
        self.assertEqual(len(members), 1)
```

The factory provides methods for creating scopes, principals, objects, rules,
environments, secrets, connectors, and more. Every method accepts optional
keyword arguments to override defaults.

## pytest fixtures

For pytest users, pyscoped provides ready-made fixtures in `scoped.testing.fixtures`.

### scoped_backend

An in-memory SQLite backend that is created and destroyed per test. Full schema
applied automatically.

```python
from scoped.testing.fixtures import scoped_backend


def test_scope_creation(scoped_backend):
    from scoped.manifest._services import build_services

    services = build_services(scoped_backend)
    p = services.principals.create_principal(kind="user", display_name="Alice", principal_id="alice")
    scope = services.scopes.create_scope(name="demo", owner_id=p.id)
    assert scope.name == "demo"
```

### scoped_services

A fully-wired `ScopedServices` instance with all 16 layers ready to use.
Depends on `scoped_backend`.

```python
from scoped.testing.fixtures import scoped_services


def test_with_services(scoped_services):
    p = scoped_services.principals.create_principal(
        kind="user", display_name="Alice", principal_id="alice",
    )
    obj, v1 = scoped_services.manager.create(
        object_type="doc", owner_id=p.id, data={"title": "Test"},
    )
    assert v1.version == 1
```

### alice, bob

Pre-built test principals for quick setup.

```python
from scoped.testing.fixtures import scoped_services, alice, bob


def test_isolation(scoped_services, alice, bob):
    obj, _ = scoped_services.manager.create(
        object_type="doc", owner_id=alice.id, data={"x": 1},
    )
    assert scoped_services.manager.get(obj.id, principal_id=alice.id) is not None
    assert scoped_services.manager.get(obj.id, principal_id=bob.id) is None
```

## Assertion helpers

pyscoped provides domain-specific assertion functions in `scoped.testing.assertions`
that produce clear, actionable error messages when they fail.

### Isolation and visibility

```python
from scoped.testing.assertions import (
    assert_isolated,
    assert_can_read,
    assert_cannot_read,
    assert_visible,
)

# Verify owner-private isolation (owner can see, other cannot)
assert_isolated(backend, object_id=doc.id, owner_id=alice.id, other_id=bob.id)

# Individual visibility checks
assert_can_read(backend, doc.id, alice.id)
assert_cannot_read(backend, doc.id, bob.id)
```

### Access control

```python
from scoped.testing.assertions import assert_access_denied

# Verify that a function raises AccessDeniedError
assert_access_denied(lambda: manager.update(obj_id, principal_id=bob_id, data={}))
```

### Audit trail

```python
from scoped.testing.assertions import (
    assert_audit_recorded,
    assert_trace_exists,
    assert_hash_chain_valid,
)

# Exact match
assert_audit_recorded(backend, actor_id=alice.id, action="create", target_id=doc.id)

# Flexible criteria (all parameters optional)
assert_trace_exists(backend, actor_id=alice.id)
assert_trace_exists(backend, action="delete", target_type="invoice")

# Verify the entire hash chain
assert_hash_chain_valid(backend)
```

### Object state

```python
from scoped.testing.assertions import (
    assert_version_count,
    assert_tombstoned,
    assert_secret_never_leaked,
)

assert_version_count(backend, doc.id, expected=3)
assert_tombstoned(backend, doc.id)
assert_secret_never_leaked(backend, secret_id)
```

## Backend markers

Use markers from `scoped.testing.markers` to run tests only against specific
backends. Register them in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "sqlite_only: Run only with SQLite backend",
    "postgres_only: Run only with PostgreSQL backend",
]
```

Then decorate tests:

```python
from scoped.testing.markers import sqlite_only, postgres_only

@sqlite_only
def test_fts5_virtual_table():
    ...  # FTS5 is SQLite-specific

@postgres_only
def test_rls_policies():
    ...  # RLS is PostgreSQL-specific
```

## Transactional fixture

The `scoped_txn` fixture wraps each test in a database transaction that rolls
back at teardown. This is faster than recreating the schema for each test:

```python
from scoped.testing.fixtures import scoped_txn

def test_with_rollback(scoped_backend, scoped_txn):
    # Any writes here are rolled back after the test
    ...
```

## Recommended patterns

### Fixture-first

Always create your test data through fixtures or the factory rather than
calling storage directly. This ensures migrations are applied and invariants
are maintained.

```python
def test_with_fixtures(sqlite_backend):
    from scoped import Client
    from scoped.testing.factory import ScopedFactory

    client = Client(backend=sqlite_backend)
    factory = ScopedFactory(client)

    scope = factory.scope(name="fixture-scope")
    obj = factory.object(scope_id=scope["id"], type="Document")
    assert obj["scope_id"] == scope["id"]
```

### Principal tuple unpacking

Factory-created principals return a `(principal_id, principal_dict)` tuple.
Unpack it for clean test code:

```python
pid, principal = factory.principal(name="Alice")
client.add_member(scope_id=scope["id"], principal_id=pid, role="editor")
```

### Global state reset (autouse fixture)

If your application uses global registry state, define an autouse fixture that
resets it between tests:

```python
import pytest
from scoped.registry import _global_registry


@pytest.fixture(autouse=True)
def reset_global_state():
    _global_registry.reset()
    yield
    _global_registry.reset()
```

## Testing with Django

### test_settings.py

Create a dedicated settings module that configures pyscoped with an in-memory
SQLite backend:

```python
# myproject/test_settings.py
from myproject.settings import *  # noqa: F401,F403

DATABASES["scoped"] = {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": ":memory:",
}

PYSCOPED_BACKEND = "sqlite"
PYSCOPED_BACKEND_OPTIONS = {"database": ":memory:"}
```

Run tests with:

```bash
DJANGO_SETTINGS_MODULE=myproject.test_settings pytest tests/
```

### Separate test database

When testing against PostgreSQL, use Django's `TEST` database configuration so
the test database is created and destroyed automatically:

```python
DATABASES["scoped"] = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": "scoped_production",
    "TEST": {"NAME": "scoped_test"},
}
```

## Testing with FastAPI

Use Starlette's `TestClient` with the pyscoped middleware applied:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient
from scoped.integrations.fastapi import ScopedMiddleware

app = FastAPI()
app.add_middleware(ScopedMiddleware, backend=sqlite_backend)


@app.get("/scopes")
def list_scopes():
    from scoped import current_client

    return current_client().list_scopes()


def test_list_scopes(sqlite_backend):
    app.add_middleware(ScopedMiddleware, backend=sqlite_backend)
    client = TestClient(app)
    resp = client.get("/scopes", headers={"X-Principal-Id": "user-1"})
    assert resp.status_code == 200
```

## Testing rules

Create a rule, bind it to a scope, and verify that access checks behave
correctly:

```python
from scoped.exceptions import AccessDeniedError
import pytest


def test_deny_rule(sqlite_backend):
    from scoped import Client
    from scoped.testing.factory import ScopedFactory

    client = Client(backend=sqlite_backend)
    factory = ScopedFactory(client)

    scope = factory.scope()
    pid, _ = factory.principal()
    factory.membership(scope_id=scope["id"], principal_id=pid, role="viewer")

    # Create a deny rule for delete operations
    client.create_rule(
        scope_id=scope["id"],
        effect="deny",
        action="delete",
        role="viewer",
    )

    # Verify enforcement
    with pytest.raises(AccessDeniedError):
        client.delete_object(
            scope_id=scope["id"],
            object_id="obj-1",
            principal_id=pid,
        )
```

## Testing audit

Verify that the audit chain maintains integrity after a sequence of operations:

```python
def test_audit_chain_integrity(sqlite_backend):
    from scoped import Client
    from scoped.testing.factory import ScopedFactory

    client = Client(backend=sqlite_backend)
    factory = ScopedFactory(client)

    scope = factory.scope()
    pid, _ = factory.principal()
    factory.membership(scope_id=scope["id"], principal_id=pid, role="admin")

    # Perform several operations
    obj = client.create_object(
        scope_id=scope["id"],
        type="Document",
        data={"title": "Test"},
        principal_id=pid,
    )
    client.update_object(
        scope_id=scope["id"],
        object_id=obj["id"],
        data={"title": "Updated"},
        principal_id=pid,
    )

    # Verify chain integrity
    trail = client.query_audit(scope_id=scope["id"])
    assert len(trail) >= 2

    # Each entry should reference the previous hash
    for i in range(1, len(trail)):
        assert trail[i]["prev_hash"] == trail[i - 1]["hash"]
```

## Example test class

A complete example combining the test case, factory, rules, and audit:

```python
from scoped.testing.base import ScopedTestCase
from scoped.testing.factory import ScopedFactory
from scoped.exceptions import AccessDeniedError


class TestDocumentWorkflow(ScopedTestCase):
    def setUp(self):
        super().setUp()
        self.factory = ScopedFactory(self.client)
        self.scope = self.factory.scope(name="engineering")
        self.admin_id, _ = self.factory.principal(name="Admin")
        self.viewer_id, _ = self.factory.principal(name="Viewer")
        self.factory.membership(
            scope_id=self.scope["id"],
            principal_id=self.admin_id,
            role="admin",
        )
        self.factory.membership(
            scope_id=self.scope["id"],
            principal_id=self.viewer_id,
            role="viewer",
        )

    def test_admin_can_create(self):
        obj = self.client.create_object(
            scope_id=self.scope["id"],
            type="Document",
            data={"title": "Spec"},
            principal_id=self.admin_id,
        )
        self.assertIsNotNone(obj["id"])

    def test_viewer_cannot_delete(self):
        obj = self.factory.object(
            scope_id=self.scope["id"],
            type="Document",
        )
        self.client.create_rule(
            scope_id=self.scope["id"],
            effect="deny",
            action="delete",
            role="viewer",
        )
        with self.assertRaises(AccessDeniedError):
            self.client.delete_object(
                scope_id=self.scope["id"],
                object_id=obj["id"],
                principal_id=self.viewer_id,
            )

    def test_audit_trail_populated(self):
        self.factory.object(
            scope_id=self.scope["id"],
            type="Document",
        )
        trail = self.client.query_audit(scope_id=self.scope["id"])
        self.assertGreater(len(trail), 0)
```
