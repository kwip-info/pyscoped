"""Layer 2: Identity & Principals.

Provides generic principal machinery, context management, and
relationship graph resolution.
"""

from scoped.identity.context import ScopedContext
from scoped.identity.principal import (
    Principal,
    PrincipalRelationship,
    PrincipalStore,
)
from scoped.identity.resolver import PrincipalResolver, ResolutionPath

__all__ = [
    "Principal",
    "PrincipalRelationship",
    "PrincipalStore",
    "PrincipalResolver",
    "ResolutionPath",
    "ScopedContext",
]
