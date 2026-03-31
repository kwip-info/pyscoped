"""Registry kind definitions.

Every registered construct has a kind. Applications can extend with custom kinds.
"""

from __future__ import annotations

from enum import Enum, auto


class RegistryKind(Enum):
    """Built-in kinds for registered constructs."""

    # Data / Schema
    MODEL = auto()          # A data model / table definition
    FIELD = auto()          # A field on a model
    RELATIONSHIP = auto()   # FK, M2M, or custom relationship between models
    INSTANCE = auto()       # A specific data object instance

    # Code
    FUNCTION = auto()       # A standalone function
    CLASS = auto()          # A class (non-model)
    METHOD = auto()         # A method on a class
    SIGNAL = auto()         # A signal / event
    TASK = auto()           # An async/background task

    # Behavioral / HTTP
    VIEW = auto()           # A view / endpoint
    SERIALIZER = auto()     # A serializer / schema
    MIDDLEWARE = auto()      # A middleware component

    # Framework internals
    PRINCIPAL = auto()      # An identity principal kind
    SCOPE = auto()          # A tenancy scope
    RULE = auto()           # A rule definition
    ENVIRONMENT = auto()    # An ephemeral/persistent workspace
    PIPELINE = auto()       # A stage pipeline definition
    STAGE = auto()          # A stage in a pipeline
    FLOW_CHANNEL = auto()   # A flow channel between points
    DEPLOYMENT = auto()     # A deployment record
    SECRET = auto()         # An encrypted secret
    SECRET_REF = auto()     # A reference handle to a secret
    INTEGRATION = auto()    # An external system connection
    PLUGIN = auto()         # A code extension
    PLUGIN_HOOK = auto()    # An extension point
    CONNECTOR = auto()      # A cross-org bridge
    MARKETPLACE_LISTING = auto()  # A published marketplace entry

    # Events / Notifications / Scheduling
    EVENT_SUBSCRIPTION = auto()   # An event subscription
    WEBHOOK_ENDPOINT = auto()     # A webhook delivery target
    NOTIFICATION_RULE = auto()    # A notification rule
    SCHEDULE = auto()             # A recurring schedule
    SCHEDULED_ACTION = auto()     # A scheduled action

    # Templates
    TEMPLATE = auto()       # A reusable blueprint for creating constructs

    # App Config
    APP_CONFIG = auto()     # Application configuration entry (auth, nav, theme, etc.)

    # Manifests
    MANIFEST = auto()       # A versioned application manifest (IaaC)

    # Extension point
    CUSTOM = auto()         # Application-defined kind


class CustomKind:
    """
    Application-defined registry kind.

    Use when the built-in RegistryKind values don't cover your construct type.
    Custom kinds must be registered themselves before use.
    """

    _registered: dict[str, CustomKind] = {}

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description

    def __repr__(self) -> str:
        return f"CustomKind({self.name!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, CustomKind):
            return self.name == other.name
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.name)

    @classmethod
    def define(cls, name: str, description: str = "") -> CustomKind:
        """Define and register a new custom kind."""
        if name in cls._registered:
            return cls._registered[name]
        kind = cls(name, description)
        cls._registered[name] = kind
        return kind

    @classmethod
    def get(cls, name: str) -> CustomKind | None:
        return cls._registered.get(name)

    @classmethod
    def all(cls) -> dict[str, CustomKind]:
        return dict(cls._registered)

    @classmethod
    def reset(cls) -> None:
        """Clear all custom kinds. Used in testing."""
        cls._registered.clear()
