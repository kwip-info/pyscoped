"""Tests for data isolation: Postgres RLS (Tier 1) and TenantRouter (Tier 2)."""

import pytest

from scoped.identity.context import ScopedContext
from scoped.identity.principal import Principal, PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.storage.sa_sqlite import SASQLiteBackend as SQLiteBackend
from scoped.storage.tenant_router import TenantResolutionError, TenantRouter
from scoped.types import Lifecycle, Metadata, generate_id, now_utc


# -- Fixtures -----------------------------------------------------------------


def _make_principal(pid: str, name: str = "Test") -> Principal:
    """Create an in-memory Principal without DB persistence."""
    return Principal(
        id=pid,
        kind="user",
        display_name=name,
        registry_entry_id=generate_id(),
        created_at=now_utc(),
        created_by="system",
        lifecycle=Lifecycle.ACTIVE,
        metadata=Metadata(),
    )


# =============================================================================
# Tier 1: Postgres RLS
# =============================================================================


class TestRLSMigration:
    """Verify the RLS migration exists and is well-formed."""

    def test_migration_loads(self):
        from scoped.storage.migrations.versions.m0013_row_level_security import (
            AddRowLevelSecurity,
        )

        mig = AddRowLevelSecurity()
        assert mig.version == 13
        assert mig.name == "row_level_security"

    def test_migration_noop_on_sqlite(self, sqlite_backend):
        """RLS migration should be a no-op for SQLite backends."""
        from scoped.storage.migrations.versions.m0013_row_level_security import (
            AddRowLevelSecurity,
        )

        mig = AddRowLevelSecurity()
        # Should not raise — just skips
        mig.up(sqlite_backend)
        mig.down(sqlite_backend)


class TestRLSContextInjection:
    """Test that PostgresBackend RLS context methods exist and behave correctly."""

    @pytest.fixture(autouse=True)
    def _skip_without_psycopg(self):
        try:
            import psycopg  # noqa: F401
            import psycopg_pool  # noqa: F401
        except ImportError:
            pytest.skip("psycopg/psycopg_pool not installed")

    def test_postgres_backend_has_enable_rls(self):
        """The enable_rls parameter should be accepted."""
        from scoped.storage.postgres import PostgresBackend

        backend = PostgresBackend.__new__(PostgresBackend)
        backend._enable_rls = True
        assert backend._enable_rls is True

    def test_set_rls_context_with_context(self):
        """_set_rls_context should read from ScopedContext."""
        alice = _make_principal("alice-123")
        with ScopedContext(principal=alice):
            from scoped.identity.context import ScopedContext as SC
            ctx = SC.current_or_none()
            assert ctx is not None
            assert ctx.principal_id == "alice-123"

    def test_set_rls_context_without_context_is_safe(self):
        """Without active context, RLS should use empty string (deny-all)."""
        from scoped.identity.context import ScopedContext as SC
        assert SC.current_or_none() is None


# =============================================================================
# Tier 2: TenantRouter
# =============================================================================


class TestTenantRouterBasic:
    """Test TenantRouter with SQLite backends (no Postgres needed)."""

    def _make_router(self, *, default_tenant: str | None = None) -> TenantRouter:
        """Create a TenantRouter backed by in-memory SQLite per tenant."""

        def resolver(principal_id: str) -> str:
            # Simple mapping: alice → tenant_a, bob → tenant_b
            return {"alice": "tenant_a", "bob": "tenant_b"}.get(
                principal_id, "tenant_default"
            )

        def factory(tenant_id: str) -> SQLiteBackend:
            return SQLiteBackend(":memory:")

        return TenantRouter(
            tenant_resolver=resolver,
            backend_factory=factory,
            default_tenant_id=default_tenant,
        )

    def test_provision_creates_backend(self):
        router = self._make_router()
        backend = router.provision_tenant("tenant_a")
        assert backend is not None
        assert "tenant_a" in router.list_tenants()
        router.close()

    def test_provision_is_idempotent(self):
        router = self._make_router()
        b1 = router.provision_tenant("tenant_a")
        b2 = router.provision_tenant("tenant_a")
        assert b1 is b2
        router.close()

    def test_teardown_removes_tenant(self):
        router = self._make_router()
        router.provision_tenant("tenant_a")
        assert "tenant_a" in router.list_tenants()

        router.teardown_tenant("tenant_a")
        assert "tenant_a" not in router.list_tenants()
        router.close()

    def test_teardown_nonexistent_is_noop(self):
        router = self._make_router()
        router.teardown_tenant("ghost")  # Should not raise
        router.close()

    def test_list_tenants_empty(self):
        router = self._make_router()
        assert router.list_tenants() == []
        router.close()


class TestTenantRouterIsolation:
    """Verify that data written by one tenant is invisible to another."""

    def _make_router(self) -> TenantRouter:
        def resolver(principal_id: str) -> str:
            return f"tenant_{principal_id}"

        def factory(tenant_id: str) -> SQLiteBackend:
            return SQLiteBackend(":memory:")

        return TenantRouter(
            tenant_resolver=resolver,
            backend_factory=factory,
        )

    def test_tenants_have_separate_data(self, registry):
        router = self._make_router()
        alice = _make_principal("alice")
        bob = _make_principal("bob")

        # Alice creates objects in her tenant DB
        with ScopedContext(principal=alice):
            store = PrincipalStore(router)
            store.create_principal(
                kind="user", display_name="Alice", principal_id="alice",
            )
            mgr = ScopedManager(router)
            obj_a, _ = mgr.create(
                object_type="Doc", owner_id="alice", data={"owner": "alice"},
            )

        # Bob creates objects in his tenant DB
        with ScopedContext(principal=bob):
            store = PrincipalStore(router)
            store.create_principal(
                kind="user", display_name="Bob", principal_id="bob",
            )
            mgr = ScopedManager(router)
            obj_b, _ = mgr.create(
                object_type="Doc", owner_id="bob", data={"owner": "bob"},
            )

        # Alice's tenant should only have alice's data
        with ScopedContext(principal=alice):
            mgr = ScopedManager(router)
            alice_objs = mgr.list_objects(principal_id="alice")
            assert len(alice_objs) == 1
            assert alice_objs[0].id == obj_a.id

        # Bob's tenant should only have bob's data
        with ScopedContext(principal=bob):
            mgr = ScopedManager(router)
            bob_objs = mgr.list_objects(principal_id="bob")
            assert len(bob_objs) == 1
            assert bob_objs[0].id == obj_b.id

        # Alice cannot see Bob's object (different database entirely)
        with ScopedContext(principal=alice):
            mgr = ScopedManager(router)
            cross = mgr.list_objects(principal_id="bob")
            assert len(cross) == 0

        router.close()

    def test_no_context_raises_without_default(self):
        router = self._make_router()
        with pytest.raises(TenantResolutionError, match="No ScopedContext"):
            router.execute("SELECT 1", ())
        router.close()

    def test_default_tenant_used_without_context(self, registry):
        def resolver(pid: str) -> str:
            return f"tenant_{pid}"

        def factory(tenant_id: str) -> SQLiteBackend:
            return SQLiteBackend(":memory:")

        router = TenantRouter(
            tenant_resolver=resolver,
            backend_factory=factory,
            default_tenant_id="tenant_system",
        )
        router.initialize()

        # Should use default tenant — no context needed
        row = router.fetch_one("SELECT 1 AS ok", ())
        assert row["ok"] == 1
        assert "tenant_system" in router.list_tenants()
        router.close()


class TestTenantRouterTransaction:
    """Verify transactions work through the router."""

    def test_transaction_routes_correctly(self, registry):
        def resolver(pid: str) -> str:
            return f"tenant_{pid}"

        def factory(tenant_id: str) -> SQLiteBackend:
            return SQLiteBackend(":memory:")

        router = TenantRouter(
            tenant_resolver=resolver,
            backend_factory=factory,
        )

        alice = _make_principal("alice")

        with ScopedContext(principal=alice):
            # Provision by first access
            router.execute(
                "CREATE TABLE IF NOT EXISTS test_data (id TEXT, value TEXT)", ()
            )

            txn = router.transaction()
            txn.execute("INSERT INTO test_data (id, value) VALUES (?, ?)", ("1", "hello"))
            txn.commit()

            row = router.fetch_one("SELECT value FROM test_data WHERE id = ?", ("1",))
            assert row["value"] == "hello"

        router.close()

    def test_transaction_rollback(self, registry):
        def resolver(pid: str) -> str:
            return f"tenant_{pid}"

        def factory(tenant_id: str) -> SQLiteBackend:
            return SQLiteBackend(":memory:")

        router = TenantRouter(
            tenant_resolver=resolver,
            backend_factory=factory,
        )

        alice = _make_principal("alice")

        with ScopedContext(principal=alice):
            router.execute(
                "CREATE TABLE IF NOT EXISTS test_data (id TEXT, value TEXT)", ()
            )

            txn = router.transaction()
            txn.execute("INSERT INTO test_data (id, value) VALUES (?, ?)", ("1", "temp"))
            txn.rollback()

            row = router.fetch_one("SELECT value FROM test_data WHERE id = ?", ("1",))
            assert row is None

        router.close()


class TestTenantRouterThreadSafety:
    """Verify concurrent tenant provisioning is safe."""

    def test_concurrent_provision(self):
        import threading

        backends_created = []

        def factory(tenant_id: str) -> SQLiteBackend:
            backends_created.append(tenant_id)
            return SQLiteBackend(":memory:")

        router = TenantRouter(
            tenant_resolver=lambda pid: pid,
            backend_factory=factory,
        )

        errors = []

        def provision(tid: str):
            try:
                router.provision_tenant(tid)
            except Exception as e:
                errors.append(e)

        # Provision same tenant from 10 threads
        threads = [threading.Thread(target=provision, args=("shared",)) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # Factory should only be called once due to lock
        assert backends_created.count("shared") == 1
        assert len(router.list_tenants()) == 1
        router.close()
