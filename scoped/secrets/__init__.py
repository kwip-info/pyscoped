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
from scoped.secrets.vault import SecretVault

__all__ = [
    "AccessResult",
    "FernetBackend",
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
]
