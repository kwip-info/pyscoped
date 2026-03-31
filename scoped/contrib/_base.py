"""Shared utilities for framework adapters."""

from __future__ import annotations

from typing import Any

from scoped.storage.interface import StorageBackend


def resolve_principal_from_id(backend: StorageBackend, principal_id: str):
    """Look up a principal by ID. Returns Principal or None."""
    from scoped.identity.principal import PrincipalStore

    store = PrincipalStore(backend)
    return store.find_principal(principal_id)


def build_services(backend: StorageBackend) -> dict[str, Any]:
    """Create the standard set of Scoped services from a backend.

    Returns a dict with keys: backend, principals, manager, scopes,
    projections, audit_writer, audit_query, rules, rule_engine, health.
    """
    from scoped.audit.query import AuditQuery
    from scoped.audit.writer import AuditWriter
    from scoped.identity.principal import PrincipalStore
    from scoped.objects.manager import ScopedManager
    from scoped.rules.engine import RuleEngine, RuleStore
    from scoped.tenancy.lifecycle import ScopeLifecycle
    from scoped.tenancy.projection import ProjectionManager
    from scoped.testing.health import HealthChecker

    audit_writer = AuditWriter(backend)
    return {
        "backend": backend,
        "principals": PrincipalStore(backend),
        "manager": ScopedManager(backend, audit_writer=audit_writer),
        "scopes": ScopeLifecycle(backend, audit_writer=audit_writer),
        "projections": ProjectionManager(backend, audit_writer=audit_writer),
        "audit_writer": audit_writer,
        "audit_query": AuditQuery(backend),
        "rules": RuleStore(backend),
        "rule_engine": RuleEngine(backend),
        "health": HealthChecker(backend),
    }
