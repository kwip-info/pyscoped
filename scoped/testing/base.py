"""ScopedTestCase — base test class with isolation helpers.

Provides pre-built principals, assertion helpers, and context managers
for writing compliance-aware tests.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

from scoped.audit.writer import AuditWriter
from scoped.exceptions import AccessDeniedError, ComplianceViolation, IsolationViolationError
from scoped.identity.context import ScopedContext
from scoped.identity.principal import Principal, PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.storage.interface import StorageBackend
from scoped.storage.sa_sqlite import SASQLiteBackend as SQLiteBackend
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.tenancy.projection import ProjectionManager
from scoped.types import ActionType, generate_id, now_utc


class ScopedTestCase:
    """Base test class with pre-built principals and assertion helpers.

    Subclass this and use the helpers to write isolation-aware tests.
    Call ``setup_scoped()`` in your test setup to initialize.
    """

    backend: StorageBackend
    audit: AuditWriter
    manager: ScopedManager
    scopes: ScopeLifecycle
    projections: ProjectionManager
    principals: PrincipalStore

    # Pre-built principals
    user_a: Principal
    user_b: Principal
    user_c: Principal

    def setup_scoped(
        self,
        backend: StorageBackend | None = None,
        *,
        full_stack: bool = False,
    ) -> None:
        """Initialize all framework components for testing.

        Parameters
        ----------
        backend:
            Storage backend. If None, creates an in-memory SQLite backend.
        full_stack:
            If True, also initializes L8-L16 services with audit writers.
        """
        if backend is None:
            backend = SQLiteBackend(":memory:")
            backend.initialize()
        self.backend = backend
        self.audit = AuditWriter(self.backend)
        self.manager = ScopedManager(self.backend, audit_writer=self.audit)
        self.scopes = ScopeLifecycle(self.backend, audit_writer=self.audit)
        self.projections = ProjectionManager(self.backend, audit_writer=self.audit)
        self.principals = PrincipalStore(self.backend, audit_writer=self.audit)

        # Create test principals
        self.user_a = self._create_principal("User A")
        self.user_b = self._create_principal("User B")
        self.user_c = self._create_principal("User C")

        if full_stack:
            self._setup_full_stack()

    def _create_principal(self, name: str) -> Principal:
        """Create a principal using PrincipalStore."""
        return self.principals.create_principal(
            kind="user",
            display_name=name,
        )

    def _setup_full_stack(self) -> None:
        """Initialize L8-L16 services with audit writers wired in."""
        from scoped.environments.lifecycle import EnvironmentLifecycle
        from scoped.flow.engine import FlowEngine
        from scoped.flow.pipeline import PipelineManager
        from scoped.deployments.executor import DeploymentExecutor
        from scoped.secrets.vault import SecretVault
        from scoped.integrations.lifecycle import PluginLifecycleManager
        from scoped.connector.bridge import ConnectorManager
        from scoped.events.bus import EventBus
        from scoped.events.subscriptions import SubscriptionManager
        from scoped.notifications.engine import NotificationEngine
        from scoped.scheduling.scheduler import Scheduler

        self.environments = EnvironmentLifecycle(self.backend, audit_writer=self.audit)
        self.flow_engine = FlowEngine(self.backend, audit_writer=self.audit)
        self.pipelines = PipelineManager(self.backend, audit_writer=self.audit)
        self.deployments = DeploymentExecutor(self.backend, audit_writer=self.audit)
        self.secrets = SecretVault(self.backend, self.manager, audit_writer=self.audit)
        self.plugins = PluginLifecycleManager(self.backend, audit_writer=self.audit)
        self.connectors = ConnectorManager(self.backend, audit_writer=self.audit)
        self.events = EventBus(self.backend, audit_writer=self.audit)
        self.subscriptions = SubscriptionManager(self.backend, audit_writer=self.audit)
        self.notifications = NotificationEngine(self.backend, audit_writer=self.audit)
        self.scheduler = Scheduler(self.backend, audit_writer=self.audit)

    @contextmanager
    def as_principal(self, principal: Principal) -> Generator[ScopedContext, None, None]:
        """Context manager to set the acting principal."""
        ctx = ScopedContext(principal)
        with ctx:
            yield ctx

    def create_object(
        self,
        object_type: str,
        *,
        owner: Principal,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Create an object owned by the given principal."""
        obj, _ = self.manager.create(
            object_type=object_type,
            owner_id=owner.id,
            data=data or {"name": f"test_{generate_id()[:8]}"},
        )
        return obj

    def read_object(self, object_id: str, *, as_principal: Principal) -> Any:
        """Read an object as the given principal."""
        return self.manager.get(object_id, principal_id=as_principal.id)

    def create_scope(
        self,
        *,
        owner: Principal,
        members: list[Principal] | None = None,
        name: str | None = None,
    ) -> Any:
        """Create a scope and optionally add members."""
        scope = self.scopes.create_scope(
            name=name or f"scope_{generate_id()[:8]}",
            owner_id=owner.id,
        )
        from scoped.tenancy.models import ScopeRole
        for member in (members or []):
            self.scopes.add_member(
                scope.id, principal_id=member.id,
                role=ScopeRole.EDITOR, granted_by=owner.id,
            )
        return scope

    def project(self, obj: Any, scope: Any) -> Any:
        """Project an object into a scope."""
        return self.projections.project(
            object_id=obj.id,
            scope_id=scope.id,
            projected_by=obj.owner_id,
        )

    # -- Assertions --------------------------------------------------------

    def assert_access_denied(self, fn: Any) -> None:
        """Assert that calling fn raises AccessDeniedError or returns None (inaccessible)."""
        try:
            result = fn()
            if result is None:
                return  # Object not visible = isolation working
            raise AssertionError(
                f"Expected access to be denied, but got result: {result}"
            )
        except (AccessDeniedError, IsolationViolationError):
            pass

    def assert_can_read(self, object_id: str, *, as_principal: Principal) -> Any:
        """Assert that the principal can read the object."""
        result = self.manager.get(object_id, principal_id=as_principal.id)
        if result is None:
            raise AssertionError(
                f"Expected principal {as_principal.id} to be able to read "
                f"object {object_id}, but got None"
            )
        return result

    def assert_cannot_read(self, object_id: str, *, as_principal: Principal) -> None:
        """Assert that the principal cannot read the object."""
        result = self.manager.get(object_id, principal_id=as_principal.id)
        if result is not None:
            raise AssertionError(
                f"Expected principal {as_principal.id} to NOT be able to read "
                f"object {object_id}, but it was accessible"
            )

    def assert_trace_exists(
        self,
        *,
        actor_id: str,
        action: str,
        target_id: str,
    ) -> None:
        """Assert that a trace entry exists matching the criteria."""
        from scoped.audit.query import AuditQuery
        from scoped.types import ActionType

        query = AuditQuery(self.backend)
        action_type = ActionType(action)
        entries = query.query(
            actor_id=actor_id,
            action=action_type,
            target_id=target_id,
            limit=1,
        )
        if not entries:
            raise AssertionError(
                f"Expected trace entry for actor={actor_id}, "
                f"action={action}, target={target_id}"
            )

    def assert_version_count(
        self,
        object_id: str,
        expected: int,
        *,
        as_principal: Principal,
    ) -> None:
        """Assert the number of versions for an object."""
        versions = self.manager.list_versions(object_id, principal_id=as_principal.id)
        if len(versions) != expected:
            raise AssertionError(
                f"Expected {expected} versions for object {object_id}, "
                f"got {len(versions)}"
            )
