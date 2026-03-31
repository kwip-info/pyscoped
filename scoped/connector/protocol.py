"""Federation protocol — message signing, verification, and schema negotiation.

This module provides the building blocks for cross-instance communication.
Actual network transport is left to application-level code; this module
handles message construction, signing, verification, and capability exchange.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from scoped.exceptions import FederationError
from scoped.types import generate_id, now_utc


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


class FederationProtocol:
    """Handles message construction, signing, and schema negotiation.

    Uses HMAC-SHA256 with a pre-shared key for message signing.
    """

    def __init__(self, shared_key: str) -> None:
        self._key = shared_key.encode("utf-8")
        self._sequence = 0

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

    def create_message(
        self,
        *,
        sender_org_id: str,
        receiver_org_id: str,
        connector_id: str,
        message_type: str,
        payload: dict[str, Any],
    ) -> FederationMessage:
        """Create and sign a federation message."""
        self._sequence += 1
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
            sequence=self._sequence,
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
            sequence=self._sequence,
            signature=sig,
        )

    def verify_message(self, message: FederationMessage) -> bool:
        """Verify a message's signature."""
        expected = self._sign(self._signing_payload(message))
        return hmac.compare_digest(expected, message.signature)

    def verify_or_raise(self, message: FederationMessage) -> None:
        """Verify a message or raise FederationError."""
        if not self.verify_message(message):
            raise FederationError(
                "Message signature verification failed",
                context={"message_id": message.id},
            )

    @staticmethod
    def negotiate_schema(
        local: SchemaCapability,
        remote: SchemaCapability,
    ) -> NegotiationResult:
        """Negotiate compatible schema between two instances."""
        local_types = set(local.object_types)
        remote_types = set(remote.object_types)
        common_types = tuple(sorted(local_types & remote_types))

        local_features = set(local.features)
        remote_features = set(remote.features)
        common_features = tuple(sorted(local_features & remote_features))

        incompatibilities: list[str] = []
        if local.api_version != remote.api_version:
            incompatibilities.append(
                f"API version mismatch: local={local.api_version}, remote={remote.api_version}"
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
