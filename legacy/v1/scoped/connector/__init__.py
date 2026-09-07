"""Layer 13: Platform Connector & Marketplace.

Cross-organization meshing. Two organizations running Scoped can connect
their rivers through governed connectors — enabling collaboration without
surrendering isolation.
"""

from scoped.connector.bridge import ConnectorManager
from scoped.connector.models import (
    Connector,
    ConnectorDirection,
    ConnectorPolicy,
    ConnectorState,
    ConnectorTraffic,
    PolicyType,
    TrafficStatus,
    TERMINAL_CONNECTOR_STATES,
    VALID_CONNECTOR_TRANSITIONS,
)
from scoped.connector.protocol import (
    FederationMessage,
    FederationProtocol,
    NegotiationResult,
    SchemaCapability,
)

__all__ = [
    "Connector",
    "ConnectorDirection",
    "ConnectorManager",
    "ConnectorPolicy",
    "ConnectorState",
    "ConnectorTraffic",
    "FederationMessage",
    "FederationProtocol",
    "NegotiationResult",
    "PolicyType",
    "SchemaCapability",
    "TERMINAL_CONNECTOR_STATES",
    "TrafficStatus",
    "VALID_CONNECTOR_TRANSITIONS",
]
