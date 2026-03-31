"""Layer 1: Universal Registry.

Everything in the framework must be registered to exist. The registry tracks
data objects, models, functions, classes, relationships, views — any construct
that participates in the scoped system.
"""

from scoped.registry.base import RegistryEntry, Registry
from scoped.registry.contracts import (
    Contract,
    ContractConstraint,
    ContractField,
    ContractStore,
    ContractVersion,
    FieldType,
    ValidationResult,
    diff_contracts,
    validate_against_version,
)
from scoped.registry.kinds import RegistryKind
from scoped.registry.decorators import register, track
from scoped.registry.templates import (
    InstantiationResult,
    Template,
    TemplateStore,
    TemplateVersion,
)

__all__ = [
    "Contract",
    "ContractConstraint",
    "ContractField",
    "ContractStore",
    "ContractVersion",
    "FieldType",
    "InstantiationResult",
    "RegistryEntry",
    "Registry",
    "RegistryKind",
    "Template",
    "TemplateStore",
    "TemplateVersion",
    "ValidationResult",
    "diff_contracts",
    "register",
    "track",
    "validate_against_version",
]
