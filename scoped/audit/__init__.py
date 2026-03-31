"""Layer 6: Audit & Trace.

Immutable, append-only, hash-chained audit trail.
Every action produces a trace entry.  No exceptions.
"""

from scoped.audit.models import TraceEntry, compute_hash
from scoped.audit.query import AuditQuery, ChainVerification
from scoped.audit.writer import AuditWriter

__all__ = [
    "AuditQuery",
    "AuditWriter",
    "ChainVerification",
    "TraceEntry",
    "compute_hash",
]
