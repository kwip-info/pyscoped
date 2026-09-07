"""Tests for the General Templates extension (A7)."""

from __future__ import annotations

import json

import pytest

from scoped.exceptions import (
    AccessDeniedError,
    TemplateInstantiationError,
    TemplateNotFoundError,
    TemplateVersionNotFoundError,
)
from scoped.registry.templates import (
    InstantiationResult,
    Template,
    TemplateStore,
    TemplateVersion,
    _deep_merge,
    template_from_row,
    version_from_row,
)
from scoped.types import Lifecycle, generate_id, now_utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_principal(backend, principal_id: str | None = None) -> str:
    """Insert a minimal principal row and return its id."""
    pid = principal_id or generate_id()
    ts = now_utc().isoformat()
    # Ensure the stub registry entry exists first (FK constraint)
    backend.execute(
        "INSERT OR IGNORE INTO registry_entries "
        "(id, urn, kind, namespace, name, registered_at, registered_by) "
        "VALUES ('reg_stub', 'scoped:MODEL:test:stub:1', 'MODEL', 'test', 'stub', ?, 'system')",
        (ts,),
    )
    backend.execute(
        "INSERT INTO principals (id, kind, display_name, registry_entry_id, created_at) "
        "VALUES (?, 'user', 'Test User', 'reg_stub', ?)",
        (pid, ts),
    )
    return pid


def _create_scope(backend, owner_id: str, scope_id: str | None = None) -> str:
    """Insert a minimal scope row and return its id."""
    sid = scope_id or generate_id()
    ts = now_utc().isoformat()
    backend.execute(
        "INSERT INTO scopes (id, name, owner_id, created_at, lifecycle) "
        "VALUES (?, 'test-scope', ?, ?, 'ACTIVE')",
        (sid, owner_id, ts),
    )
    return sid


# ===========================================================================
# Template dataclass
# ===========================================================================

class TestTemplateModel:
    def test_snapshot(self):
        ts = now_utc()
        t = Template(
            id="t1", name="My Template", description="desc",
            template_type="scope", owner_id="owner1",
            schema={"key": "value"}, current_version=1,
            created_at=ts, scope_id=None, lifecycle=Lifecycle.ACTIVE,
        )
        snap = t.snapshot()
        assert snap["id"] == "t1"
        assert snap["name"] == "My Template"
        assert snap["template_type"] == "scope"
        assert snap["schema"] == {"key": "value"}
        assert snap["lifecycle"] == "ACTIVE"

    def test_is_active(self):
        ts = now_utc()
        active = Template(
            id="t1", name="T", description="", template_type="scope",
            owner_id="o", schema={}, current_version=1,
            created_at=ts, scope_id=None, lifecycle=Lifecycle.ACTIVE,
        )
        archived = Template(
            id="t2", name="T", description="", template_type="scope",
            owner_id="o", schema={}, current_version=1,
            created_at=ts, scope_id=None, lifecycle=Lifecycle.ARCHIVED,
        )
        assert active.is_active is True
        assert archived.is_active is False

    def test_frozen(self):
        ts = now_utc()
        t = Template(
            id="t1", name="T", description="", template_type="scope",
            owner_id="o", schema={}, current_version=1,
            created_at=ts, scope_id=None, lifecycle=Lifecycle.ACTIVE,
        )
        with pytest.raises(AttributeError):
            t.id = "other"  # type: ignore[misc]


# ===========================================================================
# Row mappers
# ===========================================================================

class TestRowMappers:
    def test_template_from_row(self):
        ts = now_utc()
        row = {
            "id": "t1", "name": "Template", "description": "desc",
            "template_type": "object", "owner_id": "owner1",
            "schema_json": '{"field": "default"}',
            "current_version": 2, "created_at": ts.isoformat(),
            "scope_id": "s1", "lifecycle": "ACTIVE",
        }
        t = template_from_row(row)
        assert t.id == "t1"
        assert t.schema == {"field": "default"}
        assert t.scope_id == "s1"
        assert t.current_version == 2

    def test_version_from_row(self):
        ts = now_utc()
        row = {
            "id": "v1", "template_id": "t1", "version": 3,
            "schema_json": '{"a": 1}',
            "created_at": ts.isoformat(), "created_by": "owner1",
            "change_reason": "update fields",
        }
        v = version_from_row(row)
        assert v.version == 3
        assert v.schema == {"a": 1}
        assert v.change_reason == "update fields"


# ===========================================================================
# Deep merge utility
# ===========================================================================

class TestDeepMerge:
    def test_simple_override(self):
        base = {"a": 1, "b": 2}
        overrides = {"b": 99}
        result = _deep_merge(base, overrides)
        assert result == {"a": 1, "b": 99}

    def test_nested_merge(self):
        base = {"config": {"timeout": 30, "retries": 3}, "name": "default"}
        overrides = {"config": {"timeout": 60}}
        result = _deep_merge(base, overrides)
        assert result == {"config": {"timeout": 60, "retries": 3}, "name": "default"}

    def test_add_new_keys(self):
        base = {"a": 1}
        overrides = {"b": 2}
        result = _deep_merge(base, overrides)
        assert result == {"a": 1, "b": 2}

    def test_empty_overrides(self):
        base = {"a": 1, "b": {"c": 2}}
        result = _deep_merge(base, {})
        assert result == base
        assert result is not base  # deep copy

    def test_deep_nested(self):
        base = {"l1": {"l2": {"l3": {"value": "original", "keep": True}}}}
        overrides = {"l1": {"l2": {"l3": {"value": "replaced"}}}}
        result = _deep_merge(base, overrides)
        assert result["l1"]["l2"]["l3"]["value"] == "replaced"
        assert result["l1"]["l2"]["l3"]["keep"] is True

    def test_override_dict_with_scalar(self):
        base = {"a": {"nested": True}}
        overrides = {"a": "flat"}
        result = _deep_merge(base, overrides)
        assert result["a"] == "flat"

    def test_override_scalar_with_dict(self):
        base = {"a": "flat"}
        overrides = {"a": {"nested": True}}
        result = _deep_merge(base, overrides)
        assert result["a"] == {"nested": True}


# ===========================================================================
# TemplateStore — create
# ===========================================================================

class TestTemplateStoreCreate:
    def test_create_template(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)

        t = store.create_template(
            name="Scope Blueprint",
            template_type="scope",
            owner_id=owner,
            schema={"name_prefix": "team-", "default_role": "editor"},
            description="Standard team scope",
        )

        assert t.name == "Scope Blueprint"
        assert t.template_type == "scope"
        assert t.owner_id == owner
        assert t.schema == {"name_prefix": "team-", "default_role": "editor"}
        assert t.current_version == 1
        assert t.lifecycle == Lifecycle.ACTIVE
        assert t.scope_id is None

    def test_create_with_scope(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        scope = _create_scope(sqlite_backend, owner)
        store = TemplateStore(sqlite_backend)

        t = store.create_template(
            name="Scoped Template",
            template_type="object",
            owner_id=owner,
            schema={"type": "document"},
            scope_id=scope,
        )

        assert t.scope_id == scope

    def test_create_persists_version(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)

        t = store.create_template(
            name="T1", template_type="env", owner_id=owner, schema={"a": 1},
        )

        versions = store.list_versions(t.id)
        assert len(versions) == 1
        assert versions[0].version == 1
        assert versions[0].schema == {"a": 1}
        assert versions[0].change_reason == "initial"


# ===========================================================================
# TemplateStore — read
# ===========================================================================

class TestTemplateStoreRead:
    def test_get_template(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        t = store.create_template(
            name="T", template_type="scope", owner_id=owner, schema={"x": 1},
        )

        fetched = store.get_template(t.id)
        assert fetched.id == t.id
        assert fetched.schema == {"x": 1}

    def test_get_template_not_found(self, sqlite_backend):
        store = TemplateStore(sqlite_backend)
        with pytest.raises(TemplateNotFoundError):
            store.get_template("nonexistent")

    def test_get_version(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        t = store.create_template(
            name="T", template_type="scope", owner_id=owner, schema={"v": 1},
        )

        v = store.get_version(t.id, 1)
        assert v.version == 1
        assert v.schema == {"v": 1}

    def test_get_version_not_found(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        t = store.create_template(
            name="T", template_type="scope", owner_id=owner, schema={},
        )

        with pytest.raises(TemplateVersionNotFoundError):
            store.get_version(t.id, 99)


# ===========================================================================
# TemplateStore — update
# ===========================================================================

class TestTemplateStoreUpdate:
    def test_update_creates_new_version(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        t = store.create_template(
            name="T", template_type="scope", owner_id=owner, schema={"a": 1},
        )

        updated = store.update_template(
            t.id,
            principal_id=owner,
            schema={"a": 2, "b": 3},
            change_reason="added b",
        )

        assert updated.current_version == 2
        assert updated.schema == {"a": 2, "b": 3}

        versions = store.list_versions(t.id)
        assert len(versions) == 2
        assert versions[0].schema == {"a": 1}
        assert versions[1].schema == {"a": 2, "b": 3}
        assert versions[1].change_reason == "added b"

    def test_update_name_and_description(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        t = store.create_template(
            name="Old Name", template_type="scope", owner_id=owner,
            schema={"x": 1}, description="old desc",
        )

        updated = store.update_template(
            t.id, principal_id=owner, schema={"x": 1},
            name="New Name", description="new desc",
        )

        assert updated.name == "New Name"
        assert updated.description == "new desc"

    def test_update_non_owner_denied(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        other = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        t = store.create_template(
            name="T", template_type="scope", owner_id=owner, schema={},
        )

        with pytest.raises(AccessDeniedError):
            store.update_template(t.id, principal_id=other, schema={"new": True})

    def test_update_archived_denied(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        t = store.create_template(
            name="T", template_type="scope", owner_id=owner, schema={},
        )
        store.archive_template(t.id, principal_id=owner)

        with pytest.raises(TemplateInstantiationError):
            store.update_template(t.id, principal_id=owner, schema={"new": True})


# ===========================================================================
# TemplateStore — list / query
# ===========================================================================

class TestTemplateStoreList:
    def test_list_all(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        store.create_template(name="T1", template_type="scope", owner_id=owner, schema={})
        store.create_template(name="T2", template_type="env", owner_id=owner, schema={})

        templates = store.list_templates()
        assert len(templates) == 2

    def test_list_by_owner(self, sqlite_backend):
        owner1 = _create_principal(sqlite_backend)
        owner2 = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        store.create_template(name="T1", template_type="scope", owner_id=owner1, schema={})
        store.create_template(name="T2", template_type="scope", owner_id=owner2, schema={})

        templates = store.list_templates(owner_id=owner1)
        assert len(templates) == 1
        assert templates[0].owner_id == owner1

    def test_list_by_type(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        store.create_template(name="T1", template_type="scope", owner_id=owner, schema={})
        store.create_template(name="T2", template_type="env", owner_id=owner, schema={})
        store.create_template(name="T3", template_type="scope", owner_id=owner, schema={})

        templates = store.list_templates(template_type="scope")
        assert len(templates) == 2

    def test_list_by_scope(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        scope = _create_scope(sqlite_backend, owner)
        store = TemplateStore(sqlite_backend)
        store.create_template(name="T1", template_type="scope", owner_id=owner, schema={}, scope_id=scope)
        store.create_template(name="T2", template_type="scope", owner_id=owner, schema={})

        templates = store.list_templates(scope_id=scope)
        assert len(templates) == 1

    def test_list_excludes_archived(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        t1 = store.create_template(name="T1", template_type="scope", owner_id=owner, schema={})
        t2 = store.create_template(name="T2", template_type="scope", owner_id=owner, schema={})
        store.archive_template(t2.id, principal_id=owner)

        templates = store.list_templates()
        assert len(templates) == 1
        assert templates[0].id == t1.id

    def test_list_include_archived(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        store.create_template(name="T1", template_type="scope", owner_id=owner, schema={})
        t2 = store.create_template(name="T2", template_type="scope", owner_id=owner, schema={})
        store.archive_template(t2.id, principal_id=owner)

        templates = store.list_templates(include_archived=True)
        assert len(templates) == 2

    def test_list_empty(self, sqlite_backend):
        store = TemplateStore(sqlite_backend)
        assert store.list_templates() == []


# ===========================================================================
# TemplateStore — lifecycle
# ===========================================================================

class TestTemplateStoreLifecycle:
    def test_archive(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        t = store.create_template(name="T", template_type="scope", owner_id=owner, schema={})

        archived = store.archive_template(t.id, principal_id=owner)
        assert archived.lifecycle == Lifecycle.ARCHIVED

    def test_archive_non_owner_denied(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        other = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        t = store.create_template(name="T", template_type="scope", owner_id=owner, schema={})

        with pytest.raises(AccessDeniedError):
            store.archive_template(t.id, principal_id=other)


# ===========================================================================
# TemplateStore — instantiation
# ===========================================================================

class TestTemplateInstantiation:
    def test_instantiate_no_overrides(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        t = store.create_template(
            name="Scope Template",
            template_type="scope",
            owner_id=owner,
            schema={"name_prefix": "team-", "max_members": 10, "config": {"notify": True}},
        )

        result = store.instantiate(t.id)

        assert isinstance(result, InstantiationResult)
        assert result.template_id == t.id
        assert result.template_name == "Scope Template"
        assert result.template_type == "scope"
        assert result.template_version == 1
        assert result.data == {"name_prefix": "team-", "max_members": 10, "config": {"notify": True}}
        assert result.overrides_applied == {}

    def test_instantiate_with_overrides(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        t = store.create_template(
            name="T", template_type="scope", owner_id=owner,
            schema={"name": "default", "size": "small", "config": {"a": 1, "b": 2}},
        )

        result = store.instantiate(t.id, overrides={"name": "custom", "config": {"b": 99}})

        assert result.data == {"name": "custom", "size": "small", "config": {"a": 1, "b": 99}}
        assert result.overrides_applied == {"name": "custom", "config": {"b": 99}}

    def test_instantiate_specific_version(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        t = store.create_template(
            name="T", template_type="scope", owner_id=owner,
            schema={"version": "v1"},
        )
        store.update_template(t.id, principal_id=owner, schema={"version": "v2"})

        # Instantiate from v1
        result = store.instantiate(t.id, version=1)
        assert result.data == {"version": "v1"}
        assert result.template_version == 1

        # Instantiate from latest (v2)
        result_latest = store.instantiate(t.id)
        assert result_latest.data == {"version": "v2"}
        assert result_latest.template_version == 2

    def test_instantiate_archived_denied(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        t = store.create_template(
            name="T", template_type="scope", owner_id=owner, schema={},
        )
        store.archive_template(t.id, principal_id=owner)

        with pytest.raises(TemplateInstantiationError):
            store.instantiate(t.id)

    def test_instantiate_not_found(self, sqlite_backend):
        store = TemplateStore(sqlite_backend)
        with pytest.raises(TemplateNotFoundError):
            store.instantiate("nonexistent")

    def test_instantiate_adds_new_keys(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        t = store.create_template(
            name="T", template_type="object", owner_id=owner,
            schema={"base_key": "base_val"},
        )

        result = store.instantiate(t.id, overrides={"extra_key": "extra_val"})
        assert result.data == {"base_key": "base_val", "extra_key": "extra_val"}

    def test_instantiate_complex_schema(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        t = store.create_template(
            name="Pipeline Template",
            template_type="pipeline",
            owner_id=owner,
            schema={
                "stages": ["draft", "review", "approved"],
                "config": {
                    "auto_advance": False,
                    "notifications": {"on_enter": True, "on_exit": False},
                    "timeout_hours": 24,
                },
                "metadata": {"team": "default"},
            },
        )

        result = store.instantiate(t.id, overrides={
            "config": {
                "auto_advance": True,
                "notifications": {"on_exit": True},
            },
            "metadata": {"team": "platform", "priority": "high"},
        })

        assert result.data["stages"] == ["draft", "review", "approved"]
        assert result.data["config"]["auto_advance"] is True
        assert result.data["config"]["notifications"] == {"on_enter": True, "on_exit": True}
        assert result.data["config"]["timeout_hours"] == 24
        assert result.data["metadata"] == {"team": "platform", "priority": "high"}

    def test_instantiate_does_not_mutate_template(self, sqlite_backend):
        owner = _create_principal(sqlite_backend)
        store = TemplateStore(sqlite_backend)
        t = store.create_template(
            name="T", template_type="scope", owner_id=owner,
            schema={"config": {"mutable": [1, 2, 3]}},
        )

        result = store.instantiate(t.id, overrides={"config": {"mutable": [4, 5]}})
        assert result.data["config"]["mutable"] == [4, 5]

        # Original template schema unchanged
        refetched = store.get_template(t.id)
        assert refetched.schema["config"]["mutable"] == [1, 2, 3]
