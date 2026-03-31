"""Layer 8: Environments — ephemeral workspaces.

Environments are the unit of work: isolated workspaces where tasks happen.
Everything is throwaway until explicitly promoted.
"""

from scoped.environments.container import EnvironmentContainer
from scoped.environments.lifecycle import EnvironmentLifecycle
from scoped.environments.models import (
    Environment,
    EnvironmentObject,
    EnvironmentSnapshot,
    EnvironmentState,
    EnvironmentTemplate,
    ObjectOrigin,
)
from scoped.environments.snapshot import SnapshotManager

__all__ = [
    "Environment",
    "EnvironmentContainer",
    "EnvironmentLifecycle",
    "EnvironmentObject",
    "EnvironmentSnapshot",
    "EnvironmentState",
    "EnvironmentTemplate",
    "ObjectOrigin",
    "SnapshotManager",
]
