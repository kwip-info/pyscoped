"""Tests for ScopedContext."""

import threading

import pytest

from scoped.exceptions import NoContextError
from scoped.identity.context import ScopedContext
from scoped.identity.principal import Principal, PrincipalStore
from scoped.types import Lifecycle, Metadata


def _make_principal(store, registry, **kwargs):
    """Helper to create a principal for context tests."""
    defaults = {"kind": "user", "display_name": "Test"}
    defaults.update(kwargs)
    return store.create_principal(registry=registry, **defaults)


class TestScopedContext:
    """Tests for the context manager and global accessors."""

    def test_context_sets_principal(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        p = _make_principal(store, registry, display_name="Alice")

        with ScopedContext(principal=p):
            ctx = ScopedContext.current()
            assert ctx.principal.id == p.id
            assert ctx.principal_id == p.id
            assert ctx.principal_kind == "user"

    def test_no_context_raises(self):
        with pytest.raises(NoContextError):
            ScopedContext.current()

    def test_current_or_none_outside_context(self):
        assert ScopedContext.current_or_none() is None

    def test_context_restores_on_exit(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        p = _make_principal(store, registry)

        assert ScopedContext.current_or_none() is None
        with ScopedContext(principal=p):
            assert ScopedContext.current_or_none() is not None
        assert ScopedContext.current_or_none() is None

    def test_nested_contexts(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        alice = _make_principal(store, registry, display_name="Alice")
        bob = _make_principal(store, registry, display_name="Bob")

        with ScopedContext(principal=alice):
            assert ScopedContext.current_principal().display_name == "Alice"

            with ScopedContext(principal=bob):
                assert ScopedContext.current_principal().display_name == "Bob"

            # Restores to alice
            assert ScopedContext.current_principal().display_name == "Alice"

    def test_extras(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        p = _make_principal(store, registry)

        with ScopedContext(principal=p, environment_id="env-1", scope_id="scope-2") as ctx:
            assert ctx.extras["environment_id"] == "env-1"
            assert ctx.extras["scope_id"] == "scope-2"

    def test_require_alias(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        p = _make_principal(store, registry)

        with ScopedContext(principal=p):
            ctx = ScopedContext.require()
            assert ctx.principal.id == p.id

    def test_require_raises_without_context(self):
        with pytest.raises(NoContextError):
            ScopedContext.require()

    def test_current_principal_shortcut(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        p = _make_principal(store, registry, display_name="Direct")

        with ScopedContext(principal=p):
            assert ScopedContext.current_principal().display_name == "Direct"

    def test_thread_isolation(self, sqlite_backend, registry):
        """Contexts are isolated between threads (contextvars)."""
        store = PrincipalStore(sqlite_backend)
        p = _make_principal(store, registry, display_name="Main")
        results = {}

        def thread_fn():
            # Should not see the main thread's context
            results["thread_ctx"] = ScopedContext.current_or_none()

        with ScopedContext(principal=p):
            t = threading.Thread(target=thread_fn)
            t.start()
            t.join()

        assert results["thread_ctx"] is None
