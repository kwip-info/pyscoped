"""Tests for federation protocol — signing, verification, schema negotiation."""

import pytest

from scoped.connector.protocol import (
    FederationMessage,
    FederationProtocol,
    NegotiationResult,
    SchemaCapability,
)
from scoped.exceptions import FederationError


class TestMessageSigning:

    def test_create_signed_message(self):
        proto = FederationProtocol("shared-secret-key")
        msg = proto.create_message(
            sender_org_id="org1", receiver_org_id="org2",
            connector_id="c1", message_type="sync",
            payload={"object_type": "Document", "object_id": "doc1"},
        )
        assert msg.signature != ""
        assert msg.sender_org_id == "org1"
        assert msg.sequence == 1

    def test_verify_valid_message(self):
        proto = FederationProtocol("shared-secret-key")
        msg = proto.create_message(
            sender_org_id="org1", receiver_org_id="org2",
            connector_id="c1", message_type="sync",
            payload={"data": "test"},
        )
        assert proto.verify_message(msg) is True

    def test_verify_tampered_message(self):
        proto = FederationProtocol("shared-secret-key")
        msg = proto.create_message(
            sender_org_id="org1", receiver_org_id="org2",
            connector_id="c1", message_type="sync",
            payload={"data": "original"},
        )
        # Tamper with the message by creating a new one with different payload
        # but same signature
        tampered = FederationMessage(
            id=msg.id,
            sender_org_id=msg.sender_org_id,
            receiver_org_id=msg.receiver_org_id,
            connector_id=msg.connector_id,
            message_type=msg.message_type,
            payload={"data": "tampered"},
            timestamp=msg.timestamp,
            sequence=msg.sequence,
            signature=msg.signature,  # original signature
        )
        assert proto.verify_message(tampered) is False

    def test_verify_wrong_key(self):
        proto1 = FederationProtocol("key-one")
        proto2 = FederationProtocol("key-two")
        msg = proto1.create_message(
            sender_org_id="org1", receiver_org_id="org2",
            connector_id="c1", message_type="sync",
            payload={"data": "test"},
        )
        assert proto2.verify_message(msg) is False

    def test_verify_same_key_both_sides(self):
        proto1 = FederationProtocol("shared-key")
        proto2 = FederationProtocol("shared-key")
        msg = proto1.create_message(
            sender_org_id="org1", receiver_org_id="org2",
            connector_id="c1", message_type="heartbeat",
            payload={},
        )
        assert proto2.verify_message(msg) is True

    def test_verify_or_raise_valid(self):
        proto = FederationProtocol("key")
        msg = proto.create_message(
            sender_org_id="org1", receiver_org_id="org2",
            connector_id="c1", message_type="sync", payload={},
        )
        proto.verify_or_raise(msg)  # should not raise

    def test_verify_or_raise_invalid(self):
        proto1 = FederationProtocol("key1")
        proto2 = FederationProtocol("key2")
        msg = proto1.create_message(
            sender_org_id="org1", receiver_org_id="org2",
            connector_id="c1", message_type="sync", payload={},
        )
        with pytest.raises(FederationError, match="verification failed"):
            proto2.verify_or_raise(msg)

    def test_sequence_increments(self):
        proto = FederationProtocol("key")
        msg1 = proto.create_message(
            sender_org_id="org1", receiver_org_id="org2",
            connector_id="c1", message_type="sync", payload={},
        )
        msg2 = proto.create_message(
            sender_org_id="org1", receiver_org_id="org2",
            connector_id="c1", message_type="sync", payload={},
        )
        assert msg2.sequence == msg1.sequence + 1

    def test_to_dict(self):
        proto = FederationProtocol("key")
        msg = proto.create_message(
            sender_org_id="org1", receiver_org_id="org2",
            connector_id="c1", message_type="sync",
            payload={"key": "value"},
        )
        d = msg.to_dict()
        assert d["sender_org_id"] == "org1"
        assert d["payload"] == {"key": "value"}
        assert "signature" in d


class TestSchemaCapability:

    def test_to_dict(self):
        cap = SchemaCapability(
            object_types=("Document", "Report"),
            api_version="2.0",
            features=("streaming", "batch"),
        )
        d = cap.to_dict()
        assert d["object_types"] == ["Document", "Report"]
        assert d["api_version"] == "2.0"

    def test_from_dict(self):
        cap = SchemaCapability.from_dict({
            "object_types": ["Document"],
            "api_version": "1.0",
            "features": ["streaming"],
        })
        assert cap.object_types == ("Document",)
        assert cap.features == ("streaming",)


class TestSchemaNegotiation:

    def test_compatible(self):
        local = SchemaCapability(
            object_types=("Document", "Report", "Task"),
            api_version="1.0",
            features=("streaming", "batch"),
        )
        remote = SchemaCapability(
            object_types=("Document", "Image", "Task"),
            api_version="1.0",
            features=("batch", "webhooks"),
        )
        result = FederationProtocol.negotiate_schema(local, remote)
        assert result.compatible
        assert set(result.common_types) == {"Document", "Task"}
        assert result.common_features == ("batch",)
        assert len(result.incompatibilities) == 0

    def test_version_mismatch(self):
        local = SchemaCapability(object_types=("Document",), api_version="1.0")
        remote = SchemaCapability(object_types=("Document",), api_version="2.0")
        result = FederationProtocol.negotiate_schema(local, remote)
        assert not result.compatible
        assert "API version mismatch" in result.incompatibilities[0]

    def test_no_common_types(self):
        local = SchemaCapability(object_types=("Document",), api_version="1.0")
        remote = SchemaCapability(object_types=("Image",), api_version="1.0")
        result = FederationProtocol.negotiate_schema(local, remote)
        assert not result.compatible
        assert "No common object types" in result.incompatibilities[0]

    def test_both_empty_types(self):
        local = SchemaCapability(api_version="1.0")
        remote = SchemaCapability(api_version="1.0")
        result = FederationProtocol.negotiate_schema(local, remote)
        assert result.compatible  # no types on either side is fine

    def test_one_empty_types(self):
        local = SchemaCapability(object_types=("Document",), api_version="1.0")
        remote = SchemaCapability(api_version="1.0")
        result = FederationProtocol.negotiate_schema(local, remote)
        assert not result.compatible  # one has types, no overlap
