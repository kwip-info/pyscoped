"""Layer 0: Compliance Testing Engine.

Validates all framework invariants — at test time (static) and at runtime.
"""

from scoped.testing.base import ScopedTestCase
from scoped.testing.auditor import ComplianceAuditor
from scoped.testing.fuzzer import IsolationFuzzer
from scoped.testing.rollback import RollbackVerifier
from scoped.testing.introspection import RegistryIntrospector
from scoped.testing.middleware import ComplianceMiddleware
from scoped.testing.reports import ComplianceReport, ComplianceReporter
from scoped.testing.health import HealthChecker, HealthStatus
from scoped.testing.manifest import (
    EXTENSION_SPECS,
    LAYER_SPECS,
    ExtensionSpec,
    LayerSpec,
    get_all_tables,
    get_audit_layers,
    get_layer,
    get_layers_for_invariant,
    get_registry_layers,
)

__all__ = [
    "ScopedTestCase",
    "ComplianceAuditor",
    "IsolationFuzzer",
    "RollbackVerifier",
    "RegistryIntrospector",
    "ComplianceMiddleware",
    "ComplianceReport",
    "ComplianceReporter",
    "HealthChecker",
    "HealthStatus",
    "LayerSpec",
    "ExtensionSpec",
    "LAYER_SPECS",
    "EXTENSION_SPECS",
    "get_all_tables",
    "get_layer",
    "get_layers_for_invariant",
    "get_registry_layers",
    "get_audit_layers",
]
