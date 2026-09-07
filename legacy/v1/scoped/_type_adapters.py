"""Built-in type adapters for the Typed Object Protocol.

Each adapter knows how to serialize an instance to a JSON-compatible dict
and deserialize a dict back to a typed instance.  The registry auto-detects
the correct adapter based on the registered type.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import Any


class TypeAdapter(ABC):
    """Base class for type-specific serialization adapters."""

    @abstractmethod
    def serialize(self, obj: Any) -> dict[str, Any]:
        """Convert a typed instance to a JSON-compatible dict."""
        ...

    @abstractmethod
    def deserialize(self, cls: type, data: dict[str, Any]) -> Any:
        """Reconstruct a typed instance from a dict."""
        ...


class PydanticAdapter(TypeAdapter):
    """Adapter for ``pydantic.BaseModel`` subclasses."""

    def serialize(self, obj: Any) -> dict[str, Any]:
        return obj.model_dump(mode="json")

    def deserialize(self, cls: type, data: dict[str, Any]) -> Any:
        return cls.model_validate(data)


class DataclassAdapter(TypeAdapter):
    """Adapter for ``@dataclass`` classes."""

    def serialize(self, obj: Any) -> dict[str, Any]:
        return dataclasses.asdict(obj)

    def deserialize(self, cls: type, data: dict[str, Any]) -> Any:
        return cls(**data)


class ScopedSerializableAdapter(TypeAdapter):
    """Adapter for classes implementing the ``ScopedSerializable`` protocol."""

    def serialize(self, obj: Any) -> dict[str, Any]:
        return obj.to_scoped_dict()

    def deserialize(self, cls: type, data: dict[str, Any]) -> Any:
        return cls.from_scoped_dict(data)


def detect_adapter(cls: type) -> TypeAdapter:
    """Auto-detect the best adapter for a given type.

    Detection order:
    1. Pydantic ``BaseModel`` subclass → ``PydanticAdapter``
    2. ``@dataclass`` → ``DataclassAdapter``
    3. Implements ``ScopedSerializable`` protocol → ``ScopedSerializableAdapter``
    4. Raise ``TypeError``
    """
    # Check Pydantic (optional dependency)
    try:
        from pydantic import BaseModel

        if isinstance(cls, type) and issubclass(cls, BaseModel):
            return PydanticAdapter()
    except ImportError:
        pass

    # Check dataclass
    if dataclasses.is_dataclass(cls):
        return DataclassAdapter()

    # Check ScopedSerializable protocol
    if (
        hasattr(cls, "to_scoped_dict")
        and hasattr(cls, "from_scoped_dict")
        and callable(getattr(cls, "to_scoped_dict", None))
        and callable(getattr(cls, "from_scoped_dict", None))
    ):
        return ScopedSerializableAdapter()

    raise TypeError(
        f"Cannot auto-detect adapter for {cls.__name__!r}. "
        f"It must be a Pydantic BaseModel, a @dataclass, or implement "
        f"the ScopedSerializable protocol (to_scoped_dict / from_scoped_dict). "
        f"Alternatively, pass an explicit adapter= argument."
    )
