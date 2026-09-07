"""Tenant-routed storage backend — database-per-tenant isolation.

Provides hard isolation by routing each tenant to their own database.
The router maps the active ``ScopedContext`` principal to a tenant ID
via a configurable resolver, then delegates all storage operations to
the tenant's dedicated ``StorageBackend``.

Usage::

    from scoped.storage.tenant_router import TenantRouter
    from scoped.storage.postgres import PostgresBackend

    router = TenantRouter(
        tenant_resolver=lambda principal_id: lookup_tenant(principal_id),
        backend_factory=lambda tenant_id: PostgresBackend(
            f"postgresql://user:pass@host/{tenant_id}_db"
        ),
    )
    router.initialize()

    # With ScopedContext active, all operations route to tenant's DB
    with ScopedContext(principal=alice):
        router.execute("INSERT INTO ...", params)  # → alice's tenant DB

    # Provision a new tenant (creates DB, runs migrations)
    router.provision_tenant("tenant_123")

Architecture:
    The router maintains a cache of initialized backends keyed by tenant
    ID. When no ``ScopedContext`` is active (e.g. during system operations),
    a ``default_tenant_id`` is used if configured, otherwise operations
    raise ``TenantResolutionError``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from scoped.storage.interface import StorageBackend, StorageTransaction


class TenantResolutionError(RuntimeError):
    """Raised when the current tenant cannot be determined."""


class TenantRouter(StorageBackend):
    """Routes storage operations to per-tenant backends.

    Args:
        tenant_resolver: Callable that maps a principal_id to a tenant_id.
            Signature: ``(principal_id: str) -> str``.
        backend_factory: Callable that creates a new ``StorageBackend``
            for a given tenant_id. The factory should NOT call
            ``initialize()`` — the router handles that.
            Signature: ``(tenant_id: str) -> StorageBackend``.
        default_tenant_id: Optional fallback tenant ID when no
            ``ScopedContext`` is active. If ``None``, operations without
            context raise ``TenantResolutionError``.
    """

    def __init__(
        self,
        *,
        tenant_resolver: Callable[[str], str],
        backend_factory: Callable[[str], StorageBackend],
        default_tenant_id: str | None = None,
    ) -> None:
        self._resolver = tenant_resolver
        self._factory = backend_factory
        self._default_tenant_id = default_tenant_id
        self._backends: dict[str, StorageBackend] = {}
        self._lock = threading.Lock()

    @property
    def dialect(self) -> str:
        # Return dialect of first available backend, or "postgres" as default
        if self._backends:
            return next(iter(self._backends.values())).dialect
        return "postgres"

    # -- Tenant resolution ----------------------------------------------------

    def _resolve_tenant_id(self) -> str:
        """Determine the current tenant from ScopedContext or default."""
        from scoped.identity.context import ScopedContext

        ctx = ScopedContext.current_or_none()
        if ctx is not None:
            return self._resolver(ctx.principal_id)

        if self._default_tenant_id is not None:
            return self._default_tenant_id

        raise TenantResolutionError(
            "No ScopedContext active and no default_tenant_id configured. "
            "Cannot determine which tenant database to use."
        )

    def _get_backend(self, tenant_id: str) -> StorageBackend:
        """Get or create the backend for a tenant (thread-safe)."""
        backend = self._backends.get(tenant_id)
        if backend is not None:
            return backend

        with self._lock:
            # Double-check after acquiring lock
            backend = self._backends.get(tenant_id)
            if backend is not None:
                return backend

            backend = self._factory(tenant_id)
            backend.initialize()
            self._backends[tenant_id] = backend
            return backend

    def _current_backend(self) -> StorageBackend:
        """Get the backend for the current tenant."""
        tenant_id = self._resolve_tenant_id()
        return self._get_backend(tenant_id)

    # -- Tenant lifecycle -----------------------------------------------------

    def provision_tenant(self, tenant_id: str) -> StorageBackend:
        """Provision a new tenant: create backend, initialize schema.

        Returns the initialized backend. Idempotent — if the tenant
        already exists, returns the existing backend.
        """
        return self._get_backend(tenant_id)

    def list_tenants(self) -> list[str]:
        """Return all currently provisioned tenant IDs."""
        return list(self._backends.keys())

    def get_tenant_backend(self, tenant_id: str) -> StorageBackend | None:
        """Get the backend for a specific tenant without auto-provisioning."""
        return self._backends.get(tenant_id)

    def teardown_tenant(self, tenant_id: str) -> None:
        """Remove a tenant's backend from the router and close it.

        Does NOT drop the database — that is the caller's responsibility.
        """
        with self._lock:
            backend = self._backends.pop(tenant_id, None)
        if backend is not None:
            backend.close()

    # -- StorageBackend interface (delegates to current tenant) ----------------

    def initialize(self) -> None:
        """Initialize the default tenant if configured."""
        if self._default_tenant_id:
            self._get_backend(self._default_tenant_id)

    def transaction(self) -> StorageTransaction:
        return self._current_backend().transaction()

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> Any:
        return self._current_backend().execute(sql, params)

    def fetch_one(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> dict[str, Any] | None:
        return self._current_backend().fetch_one(sql, params)

    def fetch_all(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> list[dict[str, Any]]:
        return self._current_backend().fetch_all(sql, params)

    def close(self) -> None:
        """Close all tenant backends."""
        with self._lock:
            for backend in self._backends.values():
                backend.close()
            self._backends.clear()

    def table_exists(self, table_name: str) -> bool:
        return self._current_backend().table_exists(table_name)

    def execute_script(self, sql: str) -> None:
        self._current_backend().execute_script(sql)
