"""Layer 6: Audit & Trace.

Immutable, append-only, hash-chained audit trail.
Every action produces a trace entry.  No exceptions.
"""

from scoped.audit.models import TraceEntry, compute_hash
from scoped.audit.query import AuditQuery, ChainVerification, VerificationEntry
from scoped.audit.retention import AuditRetention, RetentionPolicy, RetentionResult
from scoped.audit.writer import AuditWriter

__all__ = [
    "AuditQuery",
    "AuditRetention",
    "AuditWriter",
    "ChainVerification",
    "RetentionPolicy",
    "RetentionResult",
    "TraceEntry",
    "VerificationEntry",
    "compute_hash",
]
