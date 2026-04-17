"""Build all 16-layer services from a storage backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scoped.audit.writer import AuditWriter
from scoped.storage.interface import StorageBackend


@dataclass(slots=True)
class ScopedServices:
    """All Scoped services wired to a single backend and audit writer."""

    backend: StorageBackend
    audit: AuditWriter

    # Lazy-initialized service instances
    _principals: Any = None
    _manager: Any = None
    _scopes: Any = None
    _projections: Any = None
    _rules: Any = None
    _rule_engine: Any = None
    _visibility_engine: Any = None
    _environments: Any = None
    _env_container: Any = None
    _env_snapshots: Any = None
    _pipelines: Any = None
    _flow: Any = None
    _promotions: Any = None
    _deployments: Any = None
    _secrets: Any = None
    _plugins: Any = None
    _connectors: Any = None
    _events: Any = None
    _subscriptions: Any = None
    _notifications: Any = None
    _scheduler: Any = None
    _webhook_fernet_key: bytes | None = None

    @property
    def webhook_key(self) -> bytes:
        """Lazily generate and cache a Fernet key for webhook config encryption.

        In production, this key should be persisted (e.g., in a database or
        environment variable). The auto-generated per-process key is suitable
        for development and testing.
        """
        if self._webhook_fernet_key is None:
            from scoped.events.crypto import generate_webhook_key
            self._webhook_fernet_key = generate_webhook_key()
        return self._webhook_fernet_key

    @property
    def principals(self) -> Any:
        if self._principals is None:
            from scoped.identity.principal import PrincipalStore
            self._principals = PrincipalStore(self.backend, audit_writer=self.audit)
        return self._principals

    @property
    def visibility_engine(self) -> Any:
        if self._visibility_engine is None:
            from scoped.tenancy.engine import VisibilityEngine
            self._visibility_engine = VisibilityEngine(self.backend)
        return self._visibility_engine

    @property
    def manager(self) -> Any:
        if self._manager is None:
            from scoped.objects.manager import ScopedManager
            self._manager = ScopedManager(
                self.backend,
                audit_writer=self.audit,
                rule_engine=self.rule_engine,
                visibility_engine=self.visibility_engine,
            )
        return self._manager

    @property
    def scopes(self) -> Any:
        if self._scopes is None:
            from scoped.tenancy.lifecycle import ScopeLifecycle
            self._scopes = ScopeLifecycle(self.backend, audit_writer=self.audit)
        return self._scopes

    @property
    def projections(self) -> Any:
        if self._projections is None:
            from scoped.tenancy.projection import ProjectionManager
            self._projections = ProjectionManager(self.backend, audit_writer=self.audit)
        return self._projections

    @property
    def rules(self) -> Any:
        if self._rules is None:
            from scoped.rules.engine import RuleStore
            self._rules = RuleStore(self.backend)
        return self._rules

    @property
    def rule_engine(self) -> Any:
        if self._rule_engine is None:
            from scoped.rules.engine import RuleEngine
            self._rule_engine = RuleEngine(self.backend, audit_writer=self.audit)
            if self._rule_engine._cache is not None:
                self.rules.set_cache(self._rule_engine._cache)
        return self._rule_engine

    @property
    def environments(self) -> Any:
        if self._environments is None:
            from scoped.environments.lifecycle import EnvironmentLifecycle
            self._environments = EnvironmentLifecycle(self.backend, audit_writer=self.audit)
        return self._environments

    @property
    def env_container(self) -> Any:
        if self._env_container is None:
            from scoped.environments.container import EnvironmentContainer
            self._env_container = EnvironmentContainer(
                self.backend, audit_writer=self.audit, rule_engine=self.rule_engine,
            )
        return self._env_container

    @property
    def env_snapshots(self) -> Any:
        if self._env_snapshots is None:
            from scoped.environments.snapshot import SnapshotManager
            self._env_snapshots = SnapshotManager(self.backend, audit_writer=self.audit)
        return self._env_snapshots

    @property
    def pipelines(self) -> Any:
        if self._pipelines is None:
            from scoped.flow.pipeline import PipelineManager
            self._pipelines = PipelineManager(self.backend, audit_writer=self.audit)
        return self._pipelines

    @property
    def flow(self) -> Any:
        if self._flow is None:
            from scoped.flow.engine import FlowEngine
            self._flow = FlowEngine(self.backend, audit_writer=self.audit)
        return self._flow

    @property
    def promotions(self) -> Any:
        if self._promotions is None:
            from scoped.flow.promotion import PromotionManager
            # Default wiring: projections are always applied; flow-channel
            # enforcement is opt-in (construct your own PromotionManager with
            # flow_engine=self.flow to require a channel for every promotion).
            self._promotions = PromotionManager(
                self.backend,
                projection_manager=self.projections,
                audit_writer=self.audit,
            )
        return self._promotions

    @property
    def deployments(self) -> Any:
        if self._deployments is None:
            from scoped.deployments.executor import DeploymentExecutor
            self._deployments = DeploymentExecutor(self.backend, audit_writer=self.audit)
        return self._deployments

    @property
    def secrets(self) -> Any:
        if self._secrets is None:
            from scoped.secrets.backend import InMemoryBackend as InMemorySecretBackend
            from scoped.secrets.vault import SecretVault
            encryption = InMemorySecretBackend()
            self._secrets = SecretVault(
                self.backend, encryption,
                object_manager=self.manager, audit_writer=self.audit,
            )
        return self._secrets

    @property
    def plugins(self) -> Any:
        if self._plugins is None:
            from scoped.integrations.lifecycle import PluginLifecycleManager
            self._plugins = PluginLifecycleManager(self.backend, audit_writer=self.audit)
        return self._plugins

    @property
    def connectors(self) -> Any:
        if self._connectors is None:
            from scoped.connector.bridge import ConnectorManager
            self._connectors = ConnectorManager(self.backend, audit_writer=self.audit)
        return self._connectors

    @property
    def events(self) -> Any:
        if self._events is None:
            from scoped.events.bus import EventBus
            self._events = EventBus(self.backend, audit_writer=self.audit)
        return self._events

    @property
    def subscriptions(self) -> Any:
        if self._subscriptions is None:
            from scoped.events.subscriptions import SubscriptionManager
            self._subscriptions = SubscriptionManager(
                self.backend,
                audit_writer=self.audit,
                webhook_key=self.webhook_key,
            )
        return self._subscriptions

    @property
    def notifications(self) -> Any:
        if self._notifications is None:
            from scoped.notifications.engine import NotificationEngine
            self._notifications = NotificationEngine(self.backend, audit_writer=self.audit)
            # Wire event bus -> notification engine pipeline.
            # Accessing self.events triggers lazy EventBus creation if needed.
            self.events.on_any(self._notifications.process_event)
        return self._notifications

    @property
    def scheduler(self) -> Any:
        if self._scheduler is None:
            from scoped.scheduling.scheduler import Scheduler
            self._scheduler = Scheduler(self.backend, audit_writer=self.audit)
        return self._scheduler


def build_services(backend: StorageBackend) -> ScopedServices:
    """Create a ScopedServices instance with all layers wired to the backend."""
    audit = AuditWriter(backend)
    return ScopedServices(backend=backend, audit=audit)
