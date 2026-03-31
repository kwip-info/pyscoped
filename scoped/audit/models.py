"""TraceEntry — the immutable record of something that happened.

Every action across every layer produces a TraceEntry.  Entries are
append-only and hash-chained for tamper detection.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from scoped.types import ActionType, generate_id, now_utc


@dataclass(slots=True)
class TraceEntry:
    """
    A single record in the audit trail.

    Immutable after creation — fields are set once and never modified.
    The ``hash`` field is computed from the entry's content plus the
    ``previous_hash``, forming a tamper-evident chain.
    """

    id: str
    sequence: int
    actor_id: str
    action: ActionType
    target_type: str
    target_id: str
    timestamp: datetime
    hash: str
    previous_hash: str = ""
    scope_id: str | None = None
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_trace_id: str | None = None

    def snapshot(self) -> dict[str, Any]:
        """Serializable snapshot of this entry."""
        return {
            "id": self.id,
            "sequence": self.sequence,
            "actor_id": self.actor_id,
            "action": self.action.value,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "scope_id": self.scope_id,
            "timestamp": self.timestamp.isoformat(),
            "before_state": self.before_state,
            "after_state": self.after_state,
            "metadata": self.metadata,
            "parent_trace_id": self.parent_trace_id,
            "hash": self.hash,
            "previous_hash": self.previous_hash,
        }


def compute_hash(
    *,
    entry_id: str,
    sequence: int,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str,
    timestamp: str,
    previous_hash: str,
    algorithm: str = "sha256",
) -> str:
    """
    Compute the hash for a trace entry.

    The hash covers the immutable identity fields plus the previous hash,
    creating a tamper-evident chain.  Changing any historical entry breaks
    the chain from that point forward.
    """
    payload = (
        f"{entry_id}|{sequence}|{actor_id}|{action}|"
        f"{target_type}|{target_id}|{timestamp}|{previous_hash}"
    )
    h = hashlib.new(algorithm)
    h.update(payload.encode("utf-8"))
    return h.hexdigest()
