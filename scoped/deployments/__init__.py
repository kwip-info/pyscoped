"""Layer 10: Deployments — graduation to external targets.

Deployments are the exit ramp. When work graduates from the Scoped
world into an external destination, it goes through the deployment layer.
Deployments are versioned, traced, gate-checked, and rollbackable.
"""

from scoped.deployments.executor import DeploymentExecutor
from scoped.deployments.gates import GateChecker, GateResult
from scoped.deployments.models import (
    Deployment,
    DeploymentGate,
    DeploymentState,
    DeploymentTarget,
    GateType,
)
from scoped.deployments.rollback import DeploymentRollbackManager

__all__ = [
    "Deployment",
    "DeploymentExecutor",
    "DeploymentGate",
    "DeploymentRollbackManager",
    "DeploymentState",
    "DeploymentTarget",
    "GateChecker",
    "GateResult",
    "GateType",
]
