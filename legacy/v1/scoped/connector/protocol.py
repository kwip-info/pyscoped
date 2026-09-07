"""Federation protocol — message signing, verification, and schema negotiation.

This module provides the building blocks for cross-instance communication.
Actual network transport is left to application-level code; this module
handles message construction, signing, verification, and capability exchange.

Replay protection
-----------------
``FederationProtocol`` is backed by two tables (added in m0016):

- ``federation_sequences`` persists the sender-side counter per
  connector so that the sequence number embedded in each signed message
  is monotonic across process restarts.
- ``federation_seen_messages`` is the receiver-side ledger of every
  ``(connector_id, sender_org_id, sequence)`` triple that has been
  accepted. Replays of previously-accepted messages raise
  ``FederationError``.

Without a backend, the protocol falls back to in-memory state — useful
for tests but not for production federation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from scoped.exceptions import FederationError
from scoped.storage._query import compile_for
from scoped.storage._schema import (
    federation_seen_messages,
    federation_sequences,
)
from scoped.storage.interface import StorageBackend
from scoped.types import generate_id, now_utc
from scoped._stability import stable


@dataclass(frozen=True, slots=True)
class FederationMessage:
    """A signed message between two Scoped instances."""

    id: str
    sender_org_id: str
    receiver_org_id: str
    connector_id: str
    message_type: str  # "sync", "schema_negotiate", "heartbeat", "revoke"
    payload: dict[str, Any]
    timestamp: datetime
    sequence: int
    signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sender_org_id": self.sender_org_id,
            "receiver_org_id": self.receiver_org_id,
            "connector_id": self.connector_id,
            "message_type": self.message_type,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
            "sequence": self.sequence,
            "signature": self.signature,
        }


@dataclass(frozen=True, slots=True)
class SchemaCapability:
    """Declares what object types and versions an instance supports."""

    object_types: tuple[str, ...] = ()
    api_version: str = "1.0"
    features: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_types": list(self.object_types),
            "api_version": self.api_version,
            "features": list(self.features),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchemaCapability:
        return cls(
            object_types=tuple(data.get("object_types", [])),
            api_version=data.get("api_version", "1.0"),
            features=tuple(data.get("features", [])),
        )


@dataclass(frozen=True, slots=True)
class NegotiationResult:
    """Result of schema negotiation between two instances."""

    compatible: bool
    common_types: tuple[str, ...] = ()
    common_features: tuple[str, ...] = ()
    incompatibilities: tuple[str, ...] = ()


@stable(since="1.8.0")
class FederationProtocol:
    """Handles message construction, signing, and schema negotiation.

    Uses HMAC-SHA256 with a pre-shared key for message signing.

    Pass a ``backend`` to enable persistent sender-sequence tracking
    and receiver-side replay protection. Without a backend, the
    sequence counter is in-memory only and ``verify_message`` will not
    detect replays.
    """

    def __init__(
        self,
        shared_key: str,
        *,
        backend: StorageBackend | None = None,
    ) -> None:
        self._key = shared_key.encode("utf-8")
        self._backend = backend
        # In-memory fallback when no backend is wired.
        self._memory_sequences: dict[str, int] = {}
        self._memory_seen: set[tuple[str, str, int]] = set()
        self._lock = threading.Lock()

    def _sign(self, data: str) -> str:
        """Compute HMAC-SHA256 signature."""
        return hmac.new(self._key, data.encode("utf-8"), hashlib.sha256).hexdigest()

    def _signing_payload(self, msg: FederationMessage) -> str:
        """Canonical string for signing (excludes signature field)."""
        parts = [
            msg.id,
            msg.sender_org_id,
            msg.receiver_org_id,
            msg.connector_id,
            msg.message_type,
            json.dumps(msg.payload, sort_keys=True),
            msg.timestamp.isoformat(),
            str(msg.sequence),
        ]
        return "|".join(parts)

    # -- Sequence persistence ---------------------------------------------

    def _next_sequence(self, connector_id: str) -> int:
        """Atomically advance and return the next sequence for a connector.

        Persists when a backend is wired; otherwise advances an
        in-memory counter. The lock guards the read-modify-write under
        concurrent senders within a single process.
        """
        with self._lock:
            if self._backend is None:
                seq = self._memory_sequences.get(connector_id, 0) + 1
                self._memory_sequences[connector_id] = seq
                return seq

            stmt = sa.select(federation_sequences.c.last_sequence).where(
                federation_sequences.c.connector_id == connector_id,
            )
            sql, params = compile_for(stmt, self._backend.dialect)
            row = self._backend.fetch_one(sql, params)

            ts = now_utc().isoformat()
            if row is None:
                seq = 1
                ins = sa.insert(federation_sequences).values(
                    connector_id=connector_id,
                    last_sequence=seq,
                    updated_at=ts,
                )
                sql, params = compile_for(ins, self._backend.dialect)
                self._backend.execute(sql, params)
            else:
                seq = int(row["last_sequence"]) + 1
                upd = sa.update(federation_sequences).where(
                    federation_sequences.c.connector_id == connector_id,
                ).values(last_sequence=seq, updated_at=ts)
                sql, params = compile_for(upd, self._backend.dialect)
                self._backend.execute(sql, params)
            return seq

    def _record_seen(
        self,
        *,
        connector_id: str,
        sender_org_id: str,
        sequence: int,
        message_id: str,
    ) -> None:
        """Mark a message as accepted on the receiver side.

        Raises ``FederationError`` if the (connector, sender, sequence)
        triple has already been seen — i.e., a replay.
        """
        if self._backend is None:
            key = (connector_id, sender_org_id, sequence)
            with self._lock:
                if key in self._memory_seen:
                    raise FederationError(
                        "Message replay detected",
                        context={
                            "connector_id": connector_id,
                            "sender_org_id": sender_org_id,
                            "sequence": sequence,
                            "message_id": message_id,
                        },
                    )
                self._memory_seen.add(key)
            return

        ins = sa.insert(federation_seen_messages).values(
            connector_id=connector_id,
            sender_org_id=sender_org_id,
            sequence=sequence,
            seen_at=now_utc().isoformat(),
            message_id=message_id,
        )
        sql, params = compile_for(ins, self._backend.dialect)
        try:
            self._backend.execute(sql, params)
        except Exception as exc:
            # PRIMARY KEY (connector_id, sender_org_id, sequence)
            # rejects replays at the database level.
            msg = str(exc).lower()
            if "unique" in msg or "primary" in msg or "integrity" in msg or "duplicate" in msg:
                raise FederationError(
                    "Message replay detected",
                    context={
                        "connector_id": connector_id,
                        "sender_org_id": sender_org_id,
                        "sequence": sequence,
                        "message_id": message_id,
                    },
                ) from exc
            raise

    # -- Public API --------------------------------------------------------

    def create_message(
        self,
        *,
        sender_org_id: str,
        receiver_org_id: str,
        connector_id: str,
        message_type: str,
        payload: dict[str, Any],
    ) -> FederationMessage:
        """Create and sign a federation message.

        The sequence number is atomically advanced per ``connector_id``
        and (when a backend is wired) persisted across restarts.
        """
        seq = self._next_sequence(connector_id)
        ts = now_utc()
        mid = generate_id()

        unsigned = FederationMessage(
            id=mid,
            sender_org_id=sender_org_id,
            receiver_org_id=receiver_org_id,
            connector_id=connector_id,
            message_type=message_type,
            payload=payload,
            timestamp=ts,
            sequence=seq,
        )

        sig = self._sign(self._signing_payload(unsigned))

        return FederationMessage(
            id=mid,
            sender_org_id=sender_org_id,
            receiver_org_id=receiver_org_id,
            connector_id=connector_id,
            message_type=message_type,
            payload=payload,
            timestamp=ts,
            sequence=seq,
            signature=sig,
        )

    def verify_message(self, message: FederationMessage) -> bool:
        """Verify a message's signature.

        Signature-only check; does not consult the replay ledger. Use
        ``accept_message`` (or ``verify_or_raise`` followed by
        ``mark_seen``) to enforce replay protection.
        """
        expected = self._sign(self._signing_payload(message))
        return hmac.compare_digest(expected, message.signature)

    def verify_or_raise(self, message: FederationMessage) -> None:
        """Verify a message or raise FederationError."""
        if not self.verify_message(message):
            raise FederationError(
                "Message signature verification failed",
                context={"message_id": message.id},
            )

    def mark_seen(self, message: FederationMessage) -> None:
        """Record that a message has been accepted on the receiver side.

        Raises ``FederationError`` if the message has already been seen.
        Callers who want signature + replay protection in one step
        should use ``accept_message`` instead.
        """
        self._record_seen(
            connector_id=message.connector_id,
            sender_org_id=message.sender_org_id,
            sequence=message.sequence,
            message_id=message.id,
        )

    def accept_message(self, message: FederationMessage) -> None:
        """Verify signature AND check for replay in a single call.

        This is the recommended entry point for receivers. Raises
        ``FederationError`` if the signature is invalid or the message
        has been seen before.
        """
        self.verify_or_raise(message)
        self.mark_seen(message)

    @staticmethod
    def negotiate_schema(
        local: SchemaCapability,
        remote: SchemaCapability,
    ) -> NegotiationResult:
        """Negotiate compatible schema between two instances.

        API versions are compared semver-style with implicit zero
        padding so ``"1.0"`` and ``"1.0.0"`` are treated as equivalent.
        Major-version differences are flagged as incompatibilities.
        """
        local_types = set(local.object_types)
        remote_types = set(remote.object_types)
        common_types = tuple(sorted(local_types & remote_types))

        local_features = set(local.features)
        remote_features = set(remote.features)
        common_features = tuple(sorted(local_features & remote_features))

        incompatibilities: list[str] = []

        local_major = _major(local.api_version)
        remote_major = _major(remote.api_version)
        if local_major is None or remote_major is None:
            if local.api_version != remote.api_version:
                incompatibilities.append(
                    f"API version mismatch: local={local.api_version}, "
                    f"remote={remote.api_version}",
                )
        elif local_major != remote_major:
            incompatibilities.append(
                f"API major version mismatch: local={local.api_version}, "
                f"remote={remote.api_version}",
            )

        if not common_types and (local_types or remote_types):
            incompatibilities.append("No common object types")

        compatible = len(incompatibilities) == 0

        return NegotiationResult(
            compatible=compatible,
            common_types=common_types,
            common_features=common_features,
            incompatibilities=tuple(incompatibilities),
        )


def _major(version: str) -> int | None:
    """Return the integer major component of a semver-like string, or None."""
    if not version:
        return None
    head = version.split(".", 1)[0]
    try:
        return int(head)
    except ValueError:
        return None
