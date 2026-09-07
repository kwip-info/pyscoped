"""Layer 11: Secrets — encrypted vault, refs, rotation, leak detection.

Secrets are the tightest isolation boundary. Values are encrypted at rest,
accessed only through opaque refs, and never appear in audit trails or
environment snapshots.
"""

from scoped.secrets.backend import FernetBackend, InMemoryBackend, SecretBackend
from scoped.secrets.leak_detection import LeakDetector
from scoped.secrets.models import (
    AccessResult,
    Secret,
    SecretAccessEntry,
    SecretClassification,
    SecretPolicy,
    SecretRef,
    SecretVersion,
)
from scoped.secrets.policy import SecretPolicyManager
from scoped.secrets.rotation import (
    make_rotation_executor,
    run_pending_rotations,
    schedule_auto_rotations,
)
from scoped.secrets.vault import SecretVault

__all__ = [
    "AccessResult",
    "AWSKMSBackend",
    "FernetBackend",
    "GCPKMSBackend",
    "InMemoryBackend",
    "LeakDetector",
    "Secret",
    "SecretAccessEntry",
    "SecretBackend",
    "SecretClassification",
    "SecretPolicy",
    "SecretPolicyManager",
    "SecretRef",
    "SecretVault",
    "SecretVersion",
    "make_rotation_executor",
    "run_pending_rotations",
    "schedule_auto_rotations",
]


def __getattr__(name: str):
    if name == "AWSKMSBackend":
        from scoped.secrets.aws_kms import AWSKMSBackend

        return AWSKMSBackend
    if name == "GCPKMSBackend":
        from scoped.secrets.gcp_kms import GCPKMSBackend

        return GCPKMSBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
