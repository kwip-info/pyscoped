"""Simplified entry point for the pyscoped framework.

This module provides ``ScopedClient`` and the module-level ``init()``
function — the primary way to start using pyscoped.

Quick start::

    import scoped

    # Initialize with zero config (in-memory SQLite)
    client = scoped.init()

    # Or with PostgreSQL
    client = scoped.init(database_url="postgresql://user:pass@localhost/mydb")

    # Create a principal (the acting user/service/agent)
    alice = scoped.principals.create("Alice")

    # Set the acting principal for a block of operations
    with scoped.as_principal(alice):
        # Create an object — it's creator-private by default
        doc, v1 = scoped.objects.create("invoice", data={"amount": 500})

        # Every mutation creates a new version
        doc, v2 = scoped.objects.update(doc.id, data={"amount": 600})

        # Create a scope and share the object
        team = scoped.scopes.create("Engineering")
        scoped.scopes.add_member(team, bob, role="editor")
        scoped.scopes.project(doc, team)

        # Query the tamper-evident audit trail
        trail = scoped.audit.for_object(doc.id)

        # Verify audit chain integrity
        assert scoped.audit.verify().valid

Architecture:
    ``ScopedClient`` wraps the internal 16-layer ``ScopedServices``
    container and exposes namespace objects (``client.objects``,
    ``client.scopes``, etc.) that provide a streamlined API with
    context-aware defaults.

    ``init()`` creates a client, sets it as the module-level default,
    and returns it. After calling ``init()``, you can use
    ``scoped.objects``, ``scoped.principals``, etc. directly at the
    module level.

    For advanced use (multiple databases, testing), create clients
    directly: ``client = scoped.ScopedClient(database_url=...)``.

API Key:
    The ``api_key`` parameter connects the SDK to the pyscoped
    management plane (dashboard, compliance reports, alerts). Format:
    ``psc_live_<32hex>`` (production) or ``psc_test_<32hex>`` (sandbox).
    The SDK works fully without an API key — it's only needed for
    management plane features and sync.

Sync:
    The management plane sync agent is built into the client. Call
    ``client.start_sync()`` to begin pushing audit metadata to the
    management plane. Sync is not required for the SDK to work.
    See ``sync_status()`` for current sync state.
"""

from __future__ import annotations

import re
from typing import Any

from scoped._namespaces.audit import AuditNamespace
from scoped._namespaces.objects import ObjectsNamespace
from scoped._namespaces.principals import PrincipalsNamespace
from scoped._namespaces.scopes import ScopesNamespace
from scoped._namespaces.secrets import SecretsNamespace
from scoped.manifest._services import ScopedServices, build_services
from scoped.storage.interface import StorageBackend

# API key validation pattern
_API_KEY_RE = re.compile(r"^psc_(live|test)_[0-9a-f]{32}$")


class ScopedClient:
    """The main entry point for using pyscoped.

    A ``ScopedClient`` wraps a storage backend and provides namespace
    objects for all framework operations. It handles backend
    initialization, service wiring, and context management.

    Construction::

        # Zero-config (in-memory SQLite)
        client = ScopedClient()

        # SQLite file
        client = ScopedClient(database_url="sqlite:///app.db")

        # PostgreSQL
        client = ScopedClient(database_url="postgresql://user:pass@host/db")

        # Pre-built backend (advanced)
        client = ScopedClient(backend=my_custom_backend)

    Namespaces:
        - ``client.principals`` — create and manage identities
        - ``client.objects`` — versioned, isolated data objects
        - ``client.scopes`` — tenancy, sharing, and access control
        - ``client.audit`` — query the tamper-evident audit trail
        - ``client.secrets`` — encrypted vault with zero-trust access

    Context management::

        with client.as_principal(alice):
            # All operations in this block are attributed to alice
            doc, v1 = client.objects.create("invoice", data={...})

    Escape hatch::

        # Access the raw 16-layer service container
        raw_manager = client.services.manager
        raw_scopes = client.services.scopes

    Cleanup::

        client.close()
        # Or use as context manager:
        with ScopedClient() as client:
            ...
    """

    def __init__(
        self,
        *,
        database_url: str | None = None,
        api_key: str | None = None,
        backend: StorageBackend | None = None,
        sync_config: Any | None = None,
    ) -> None:
        """Initialize a pyscoped client.

        Provide either ``database_url`` (parsed automatically) or
        ``backend`` (a pre-built ``StorageBackend`` instance). If
        neither is given, an in-memory SQLite database is used.

        Args:
            database_url: Database connection URL. Supported schemes:

                - ``None`` or omitted — in-memory SQLite (zero config)
                - ``"sqlite:///path/to/db"`` — SQLite file database
                - ``"sqlite:///:memory:"`` — explicit in-memory SQLite
                - ``"postgresql://user:pass@host:5432/db"`` — PostgreSQL
                - ``"postgres://..."`` — alias for ``postgresql://``

            api_key: Management plane API key. Format:
                     ``psc_live_<32hex>`` (production) or
                     ``psc_test_<32hex>`` (sandbox). Optional — the SDK
                     works fully without it.

            backend: A pre-built ``StorageBackend`` instance. If
                     provided, ``database_url`` is ignored. The backend
                     must already be initialized (``initialize()`` called).

        Raises:
            ValueError: If ``database_url`` has an unsupported scheme
                        or ``api_key`` has an invalid format.
        """
        # Validate and store API key
        self._api_key: str | None = None
        if api_key is not None:
            if not _API_KEY_RE.match(api_key):
                raise ValueError(
                    f"Invalid API key format. Expected 'psc_live_<32hex>' or "
                    f"'psc_test_<32hex>', got: {api_key[:20]}..."
                )
            self._api_key = api_key

        # Build or accept backend
        if backend is not None:
            self._backend = backend
            self._owns_backend = False
        else:
            self._backend = _create_backend(database_url)
            self._backend.initialize()
            self._owns_backend = True

        # Sync config
        self._sync_config = sync_config
        self._sync_agent: Any = None

        # Wire services
        self._services = build_services(self._backend)

        # Lazy namespace caches
        self._principals_ns: PrincipalsNamespace | None = None
        self._objects_ns: ObjectsNamespace | None = None
        self._scopes_ns: ScopesNamespace | None = None
        self._audit_ns: AuditNamespace | None = None
        self._secrets_ns: SecretsNamespace | None = None

    # -- Namespace properties ----------------------------------------------

    @property
    def principals(self) -> PrincipalsNamespace:
        """Principal (identity) management — create users, teams, services."""
        if self._principals_ns is None:
            self._principals_ns = PrincipalsNamespace(self._services)
        return self._principals_ns

    @property
    def objects(self) -> ObjectsNamespace:
        """Versioned, isolated data objects — create, read, update, delete."""
        if self._objects_ns is None:
            self._objects_ns = ObjectsNamespace(self._services)
        return self._objects_ns

    @property
    def scopes(self) -> ScopesNamespace:
        """Scopes, membership, and projection — sharing and tenancy."""
        if self._scopes_ns is None:
            self._scopes_ns = ScopesNamespace(self._services)
        return self._scopes_ns

    @property
    def audit(self) -> AuditNamespace:
        """Tamper-evident audit trail — query and verify."""
        if self._audit_ns is None:
            self._audit_ns = AuditNamespace(self._services)
        return self._audit_ns

    @property
    def secrets(self) -> SecretsNamespace:
        """Encrypted vault — create, rotate, and resolve secrets."""
        if self._secrets_ns is None:
            self._secrets_ns = SecretsNamespace(self._services)
        return self._secrets_ns

    # -- Context management ------------------------------------------------

    def as_principal(self, principal: Any) -> Any:
        """Set the acting principal for a block of operations.

        Returns a context manager. Inside the block, all operations
        that accept ``principal_id``, ``owner_id``, ``granted_by``, etc.
        will default to this principal's ID.

        Args:
            principal: A ``Principal`` object (as returned by
                       ``client.principals.create(...)``).

        Returns:
            A ``ScopedContext`` context manager.

        Example::

            alice = client.principals.create("Alice")
            with client.as_principal(alice):
                doc, _ = client.objects.create("invoice", data={...})
                # doc.owner_id == alice.id (inferred from context)
        """
        from scoped.identity.context import ScopedContext

        return ScopedContext(principal=principal)

    # -- Escape hatch ------------------------------------------------------

    @property
    def services(self) -> ScopedServices:
        """Access the raw ``ScopedServices`` container.

        Use this to reach internal services not exposed through
        namespaces (environments, pipelines, deployments, etc.) or
        when you need the underlying service directly.

        Example::

            raw_manager = client.services.manager  # ScopedManager
            raw_rules = client.services.rules      # RuleStore
        """
        return self._services

    @property
    def backend(self) -> StorageBackend:
        """The storage backend this client is connected to."""
        return self._backend

    @property
    def api_key(self) -> str | None:
        """The management plane API key, or ``None`` if not configured."""
        return self._api_key

    # -- Sync --------------------------------------------------------------

    def _ensure_sync_agent(self) -> Any:
        """Lazy-init the sync agent on first use."""
        if self._sync_agent is None:
            from scoped.exceptions import SyncNotConfiguredError
            from scoped.sync.agent import SyncAgent
            from scoped.sync.config import SyncConfig

            if self._api_key is None:
                raise SyncNotConfiguredError(
                    "Cannot start sync without an API key. "
                    "Pass api_key to scoped.init() or ScopedClient()."
                )
            config = self._sync_config or SyncConfig()
            self._sync_agent = SyncAgent(
                backend=self._backend,
                api_key=self._api_key,
                config=config,
            )
        return self._sync_agent

    def start_sync(self) -> None:
        """Start syncing audit metadata to the management plane.

        Requires an ``api_key`` to be configured. The sync agent runs
        in the background and pushes audit entries, resource counts,
        and chain hashes to the management plane API.

        Raises:
            SyncNotConfiguredError: If no ``api_key`` is set.
            SyncError: If sync is already running.
        """
        self._ensure_sync_agent().start()

    def pause_sync(self) -> None:
        """Temporarily pause sync without losing state."""
        self._ensure_sync_agent().pause()

    def resume_sync(self) -> None:
        """Resume sync from where it was paused."""
        self._ensure_sync_agent().resume()

    def stop_sync(self) -> None:
        """Stop sync and clean up resources."""
        if self._sync_agent is not None:
            self._sync_agent.stop()

    def sync_status(self) -> dict[str, Any]:
        """Return the current sync state.

        Returns:
            A dict with sync state fields. When sync is not configured
            (no API key), returns ``{"status": "not_configured"}``.
        """
        if self._api_key is None or self._sync_agent is None:
            return {"status": "not_configured"}
        return self._sync_agent.status().model_dump(mode="json")

    def verify_sync(self) -> Any:
        """Verify that synced data matches local audit chain.

        Compares the local audit hash chain against what was reported
        to the management plane, confirming nothing was dropped or
        tampered with in transit.

        Returns:
            A ``SyncVerifyResponse`` with ``verified`` (bool) and
            chain hash comparison details.
        """
        return self._ensure_sync_agent().verify()

    # -- Lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Close the client and release resources.

        Stops the sync agent if running. If the client created the
        backend (via ``database_url``), the backend is closed. If the
        backend was passed in, it is not closed (the caller is
        responsible).
        """
        if self._sync_agent is not None:
            self._sync_agent.stop()
        if self._owns_backend:
            self._backend.close()

    def __enter__(self) -> ScopedClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        backend_type = type(self._backend).__name__
        sync = "syncing" if self._api_key else "local"
        return f"<ScopedClient backend={backend_type} mode={sync}>"


# =========================================================================
# Module-level init / global default
# =========================================================================

_default_client: ScopedClient | None = None


def init(
    *,
    database_url: str | None = None,
    api_key: str | None = None,
    backend: StorageBackend | None = None,
    sync_config: Any | None = None,
) -> ScopedClient:
    """Initialize pyscoped and set the global default client.

    This is the recommended way to start using pyscoped. After calling
    ``init()``, you can use module-level access:

    - ``scoped.principals.create(...)``
    - ``scoped.objects.create(...)``
    - ``scoped.scopes.create(...)``
    - ``scoped.audit.for_object(...)``
    - ``with scoped.as_principal(...):``

    Args:
        database_url: Database connection URL. See ``ScopedClient``
                      for supported schemes. Defaults to in-memory SQLite.
        api_key: Management plane API key (optional).
        backend: Pre-built ``StorageBackend`` (advanced, overrides
                 ``database_url``).
        sync_config: ``SyncConfig`` instance for the sync agent.
                     If omitted, defaults are used when sync is started.

    Returns:
        The initialized ``ScopedClient`` instance.

    Example::

        import scoped

        # Zero-config start
        scoped.init()

        # Production PostgreSQL with management plane sync
        scoped.init(
            database_url="postgresql://user:pass@db.example.com/myapp",
            api_key="psc_live_a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
        )
        scoped.client.start_sync()
    """
    global _default_client
    client = ScopedClient(
        database_url=database_url,
        api_key=api_key,
        backend=backend,
        sync_config=sync_config,
    )
    _default_client = client
    return client


def _get_default_client() -> ScopedClient:
    """Return the global default client, or raise if not initialized.

    This is called internally by ``scoped.__getattr__`` to proxy
    module-level attribute access.
    """
    if _default_client is None:
        raise RuntimeError(
            "No pyscoped client initialized. Call scoped.init() first."
        )
    return _default_client


# =========================================================================
# URL parsing
# =========================================================================

def _create_backend(database_url: str | None) -> StorageBackend:
    """Parse a database URL and return the appropriate backend.

    Supports:
        - ``None`` → in-memory SQLite
        - ``"sqlite:///path/to/db"`` → SQLite file
        - ``"sqlite:///:memory:"`` → in-memory SQLite
        - ``"postgresql://..."`` or ``"postgres://..."`` → PostgreSQL

    The backend is returned un-initialized — the caller must call
    ``backend.initialize()``.
    """
    if database_url is None:
        from scoped.storage.sqlite import SQLiteBackend

        return SQLiteBackend(":memory:")

    if database_url.startswith("sqlite"):
        from scoped.storage.sqlite import SQLiteBackend

        # Parse path from sqlite:///path or sqlite:///:memory:
        if ":///" in database_url:
            path = database_url.split(":///", 1)[1]
        else:
            path = ":memory:"
        return SQLiteBackend(path or ":memory:")

    if database_url.startswith(("postgresql://", "postgres://")):
        from scoped.storage.postgres import PostgresBackend

        return PostgresBackend(database_url)

    raise ValueError(
        f"Unsupported database URL scheme: {database_url.split('://')[0]}://. "
        f"Supported: sqlite:///, postgresql://, postgres://"
    )
