"""Tests for Pydantic schema bridges."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from scoped.contrib.fastapi.schemas import (
    HealthCheckSchema,
    HealthStatusSchema,
    PrincipalSchema,
    ScopedObjectSchema,
    ScopeSchema,
    TraceEntrySchema,
)
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.storage.sqlite import SQLiteBackend
from scoped.tenancy.lifecycle import ScopeLifecycle


@pytest.fixture
def schema_backend():
    backend = SQLiteBackend(":memory:")
    backend.initialize()
    yield backend
    backend.close()


class TestPrincipalSchema:
    def test_from_principal(self, schema_backend):
        store = PrincipalStore(schema_backend)
        user = store.create_principal(kind="user", display_name="Schema User")

        schema = PrincipalSchema.from_principal(user)

        assert schema.id == user.id
        assert schema.kind == "user"
        assert schema.display_name == "Schema User"
        assert schema.lifecycle == "ACTIVE"


class TestScopedObjectSchema:
    def test_from_object(self, schema_backend):
        store = PrincipalStore(schema_backend)
        user = store.create_principal(kind="user", display_name="Obj User")
        manager = ScopedManager(schema_backend)
        obj, _ = manager.create(object_type="doc", owner_id=user.id, data={"x": 1})

        schema = ScopedObjectSchema.from_object(obj)

        assert schema.id == obj.id
        assert schema.object_type == "doc"
        assert schema.owner_id == user.id
        assert schema.current_version == 1


class TestScopeSchema:
    def test_from_scope(self, schema_backend):
        store = PrincipalStore(schema_backend)
        user = store.create_principal(kind="user", display_name="Scope User")
        scopes = ScopeLifecycle(schema_backend)
        scope = scopes.create_scope(name="Test Scope", owner_id=user.id)

        schema = ScopeSchema.from_scope(scope)

        assert schema.id == scope.id
        assert schema.name == "Test Scope"
        assert schema.owner_id == user.id


class TestTraceEntrySchema:
    def test_from_entry(self, schema_backend):
        from scoped.audit.writer import AuditWriter
        from scoped.audit.query import AuditQuery
        from scoped.types import ActionType

        store = PrincipalStore(schema_backend)
        user = store.create_principal(kind="user", display_name="Trace User")
        writer = AuditWriter(schema_backend)
        writer.record(
            actor_id=user.id, action=ActionType.CREATE,
            target_type="test", target_id="t1",
        )

        query = AuditQuery(schema_backend)
        entries = query.query(limit=1)
        assert len(entries) == 1

        schema = TraceEntrySchema.from_entry(entries[0])
        assert schema.actor_id == user.id
        assert schema.action == "create"


class TestHealthSchemas:
    def test_health_status_schema(self):
        status = HealthStatusSchema(
            healthy=True,
            checks={
                "db": HealthCheckSchema(name="db", passed=True, detail="OK"),
            },
        )
        assert status.healthy
        assert status.checks["db"].passed
