"""Tests for ScopedClient and scoped.init()."""

from __future__ import annotations

import pytest

import scoped
from scoped.client import ScopedClient, _default_client, init


@pytest.fixture(autouse=True)
def _reset_global():
    """Reset the global default client between tests."""
    import scoped.client

    original = scoped.client._default_client
    yield
    scoped.client._default_client = original


class TestScopedClientInit:
    def test_default_sqlite_inmemory(self):
        client = ScopedClient()
        assert client.backend.dialect == "sqlite"
        client.close()

    def test_sqlite_url(self, tmp_path):
        db_path = tmp_path / "test.db"
        client = ScopedClient(database_url=f"sqlite:///{db_path}")
        assert client.backend.dialect == "sqlite"
        client.close()

    def test_sqlite_memory_url(self):
        client = ScopedClient(database_url="sqlite:///:memory:")
        assert client.backend.dialect == "sqlite"
        client.close()

    def test_postgres_url_parsing(self):
        """Verify that a postgresql:// URL would select PostgresBackend.

        We can't actually connect without a Postgres server, so just
        test that non-postgresql URLs are rejected.
        """
        with pytest.raises(ValueError, match="Unsupported"):
            ScopedClient(database_url="mysql://localhost/db")

    def test_invalid_url_scheme(self):
        with pytest.raises(ValueError, match="Unsupported"):
            ScopedClient(database_url="redis://localhost")

    def test_pre_built_backend(self, sqlite_backend):
        client = ScopedClient(backend=sqlite_backend)
        assert client.backend is sqlite_backend
        # Does NOT close the backend (caller owns it)
        client.close()

    def test_context_manager(self):
        with ScopedClient() as client:
            assert client.backend is not None
        # Backend closed after exit


class TestApiKey:
    def test_valid_live_key(self):
        key = "psc_live_" + "a1" * 16
        client = ScopedClient(api_key=key)
        assert client.api_key == key
        client.close()

    def test_valid_test_key(self):
        key = "psc_test_" + "b2" * 16
        client = ScopedClient(api_key=key)
        assert client.api_key == key
        client.close()

    def test_no_key(self):
        client = ScopedClient()
        assert client.api_key is None
        client.close()

    def test_invalid_key_format(self):
        with pytest.raises(ValueError, match="Invalid API key"):
            ScopedClient(api_key="bad-key")

    def test_invalid_key_too_short(self):
        with pytest.raises(ValueError, match="Invalid API key"):
            ScopedClient(api_key="psc_live_tooshort")


class TestModuleLevelInit:
    def test_init_sets_global(self):
        client = init()
        import scoped.client

        assert scoped.client._default_client is client
        client.close()

    def test_module_level_access_after_init(self):
        init()
        # Should not raise
        assert scoped.principals is not None
        assert scoped.objects is not None
        assert scoped.scopes is not None
        assert scoped.audit is not None
        assert scoped.secrets is not None

    def test_module_level_access_before_init(self):
        import scoped.client

        scoped.client._default_client = None
        with pytest.raises(RuntimeError, match="Call scoped.init"):
            _ = scoped.principals

    def test_init_returns_client(self):
        client = init()
        assert isinstance(client, ScopedClient)
        client.close()


class TestMultipleClients:
    def test_separate_backends(self):
        c1 = ScopedClient()
        c2 = ScopedClient()

        alice = c1.principals.create("Alice")
        bob = c2.principals.create("Bob")

        # Each client has its own database
        assert c1.principals.find(bob.id) is None
        assert c2.principals.find(alice.id) is None

        c1.close()
        c2.close()


class TestAsPrincipal:
    def test_sets_context(self):
        with ScopedClient() as client:
            alice = client.principals.create("Alice")
            with client.as_principal(alice):
                from scoped.identity.context import ScopedContext

                ctx = ScopedContext.current()
                assert ctx.principal_id == alice.id

    def test_context_clears_after_exit(self):
        with ScopedClient() as client:
            alice = client.principals.create("Alice")
            with client.as_principal(alice):
                pass
            from scoped.identity.context import ScopedContext

            assert ScopedContext.current_or_none() is None


class TestSyncStubs:
    def test_start_sync_not_implemented(self):
        with ScopedClient() as client:
            with pytest.raises(NotImplementedError, match="0.3.0"):
                client.start_sync()

    def test_sync_status_returns_not_configured(self):
        with ScopedClient() as client:
            status = client.sync_status()
            assert status["status"] == "not_configured"

    def test_verify_sync_not_implemented(self):
        with ScopedClient() as client:
            with pytest.raises(NotImplementedError):
                client.verify_sync()


class TestRepr:
    def test_local_mode(self):
        with ScopedClient() as client:
            r = repr(client)
            assert "SQLiteBackend" in r
            assert "local" in r

    def test_syncing_mode(self):
        key = "psc_live_" + "a1" * 16
        with ScopedClient(api_key=key) as client:
            r = repr(client)
            assert "syncing" in r
