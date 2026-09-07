"""Tests for registry persistence (stores)."""

import pytest

from scoped.registry.base import Registry
from scoped.registry.kinds import CustomKind, RegistryKind
from scoped.registry.sqlite_store import SQLiteRegistryStore
from scoped.registry.store import InMemoryRegistryStore
from scoped.storage.sa_sqlite import SASQLiteBackend as SQLiteBackend
from scoped.types import Lifecycle


class TestInMemoryStore:
    def test_save_and_load(self, registry: Registry):
        store = InMemoryRegistryStore()

        entry = registry.register(kind=RegistryKind.MODEL, namespace="app", name="User")
        store.save_entry(entry)

        loaded = store.load_all()
        assert len(loaded) == 1
        assert loaded[0]["urn"] == "scoped:MODEL:app:User:1"

    def test_persist_and_hydrate(self):
        # Create and populate a registry
        reg1 = Registry()
        reg1.register(kind=RegistryKind.MODEL, namespace="app", name="M1", tags={"core"})
        reg1.register(kind=RegistryKind.FUNCTION, namespace="app", name="F1")

        # Persist
        store = InMemoryRegistryStore()
        count = store.persist_registry(reg1)
        assert count == 2

        # Hydrate into a new registry
        reg2 = Registry()
        loaded = store.hydrate_registry(reg2)
        assert loaded == 2
        assert reg2.count() == 2
        assert reg2.contains_urn("scoped:MODEL:app:M1:1")
        assert reg2.contains_urn("scoped:FUNCTION:app:F1:1")

    def test_delete_entry(self, registry: Registry):
        store = InMemoryRegistryStore()
        entry = registry.register(kind=RegistryKind.MODEL, namespace="app", name="User")
        store.save_entry(entry)
        store.delete_entry(entry.id)
        assert len(store.load_all()) == 0

    def test_clear(self, registry: Registry):
        store = InMemoryRegistryStore()
        entry = registry.register(kind=RegistryKind.MODEL, namespace="app", name="User")
        store.save_entry(entry)
        store.clear()
        assert len(store.load_all()) == 0


class TestSQLiteRegistryStore:
    def test_save_and_load(self, sqlite_backend: SQLiteBackend):
        store = SQLiteRegistryStore(sqlite_backend)
        reg = Registry()

        entry = reg.register(
            kind=RegistryKind.MODEL,
            namespace="app",
            name="User",
            metadata={"table": "users"},
            tags={"core", "auth"},
        )
        store.save_entry(entry)

        loaded = store.load_all()
        assert len(loaded) == 1
        assert loaded[0]["urn"] == "scoped:MODEL:app:User:1"
        assert loaded[0]["metadata"] == {"table": "users"}
        assert set(loaded[0]["tags"]) == {"core", "auth"}

    def test_persist_and_hydrate_roundtrip(self, sqlite_backend: SQLiteBackend):
        store = SQLiteRegistryStore(sqlite_backend)

        reg1 = Registry()
        reg1.register(kind=RegistryKind.MODEL, namespace="app", name="M1")
        reg1.register(kind=RegistryKind.MODEL, namespace="app", name="M2")
        reg1.register(kind=RegistryKind.FUNCTION, namespace="core", name="F1")
        store.persist_registry(reg1)

        reg2 = Registry()
        count = store.hydrate_registry(reg2)
        assert count == 3
        assert reg2.count() == 3

    def test_save_all_is_transactional(self, sqlite_backend: SQLiteBackend):
        store = SQLiteRegistryStore(sqlite_backend)
        reg = Registry()

        e1 = reg.register(kind=RegistryKind.MODEL, namespace="app", name="M1")
        e2 = reg.register(kind=RegistryKind.MODEL, namespace="app", name="M2")
        store.save_all([e1, e2])

        loaded = store.load_all()
        assert len(loaded) == 2

    def test_delete_and_clear(self, sqlite_backend: SQLiteBackend):
        store = SQLiteRegistryStore(sqlite_backend)
        reg = Registry()

        e1 = reg.register(kind=RegistryKind.MODEL, namespace="app", name="M1")
        e2 = reg.register(kind=RegistryKind.MODEL, namespace="app", name="M2")
        store.save_all([e1, e2])

        store.delete_entry(e1.id)
        assert len(store.load_all()) == 1

        store.clear()
        assert len(store.load_all()) == 0
