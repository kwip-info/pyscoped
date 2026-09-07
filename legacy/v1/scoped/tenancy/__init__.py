"""Layer 4: Scoping & Tenancy.

The sharing primitive.  To share anything, create a Scope, add members,
and project objects into it.  No implicit access — ever.
"""

from scoped.tenancy.config import (
    ConfigResolver,
    ConfigStore,
    ResolvedSetting,
    ScopedSetting,
)
from scoped.tenancy.engine import VisibilityEngine
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.tenancy.models import (
    AccessLevel,
    Scope,
    ScopeMembership,
    ScopeProjection,
    ScopeRole,
)
from scoped.tenancy.projection import ProjectionManager

__all__ = [
    "AccessLevel",
    "ConfigResolver",
    "ConfigStore",
    "ProjectionManager",
    "ResolvedSetting",
    "Scope",
    "ScopeLifecycle",
    "ScopeMembership",
    "ScopeProjection",
    "ScopeRole",
    "ScopedSetting",
    "VisibilityEngine",
]
