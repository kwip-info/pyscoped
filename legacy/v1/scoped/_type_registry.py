"""Typed Object Protocol — registry for binding object types to Python types.

The type registry maps ``object_type`` strings (e.g. ``"invoice"``) to
Python types (Pydantic models, dataclasses, or any class implementing
``ScopedSerializable``).  Once registered, objects of that type can be
created with typed instances and retrieved with typed access via
``ObjectVersion.typed_data``.

Usage::

    import scoped
    from pydantic import BaseModel

    class Invoice(BaseModel):
        amount: float
        currency: str

    scoped.register_type("invoice", Invoice)

    # Create with typed data (auto-serializes)
    doc, v1 = scoped.objects.create("invoice", data=Invoice(amount=500, currency="USD"))

    # Read with typed access
    versions = scoped.objects.versions(doc.id)
    invoice = versions[0].typed_data  # Invoice(amount=500, currency="USD")

    # Dict path still works
    raw = versions[0].data  # {"amount": 500, "currency": "USD"}
"""

from __future__ import annotations

import threading
from typing import Any

from scoped._type_adapters import TypeAdapter, detect_adapter


class TypeRegistry:
    """Registry mapping object_type strings to Python types + adapters.

    Thread-safe.  A module-level singleton is provided as ``_registry``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._types: dict[str, type] = {}
        self._adapters: dict[str, TypeAdapter] = {}

    def register(
        self,
        object_type: str,
        cls: type,
        *,
        adapter: TypeAdapter | None = None,
    ) -> None:
        """Bind an ``object_type`` string to a Python type.

        If no ``adapter`` is provided, one is auto-detected from the class
        (Pydantic BaseModel → PydanticAdapter, @dataclass → DataclassAdapter,
        ScopedSerializable → ScopedSerializableAdapter).

        Args:
            object_type: The string used in ``scoped.objects.create(object_type, ...)``.
            cls: The Python class to bind.
            adapter: Optional explicit adapter.  Auto-detected if omitted.

        Raises:
            TypeError: If no adapter can be detected and none is provided.
        """
        resolved_adapter = adapter or detect_adapter(cls)
        with self._lock:
            self._types[object_type] = cls
            self._adapters[object_type] = resolved_adapter

    def has_type(self, object_type: str) -> bool:
        """Check if an object type has a registered Python type."""
        with self._lock:
            return object_type in self._types

    def get_type(self, object_type: str) -> type | None:
        """Get the registered Python type for an object type, or None."""
        with self._lock:
            return self._types.get(object_type)

    def serialize(self, object_type: str, obj: Any) -> dict[str, Any]:
        """Serialize a typed instance to a JSON-compatible dict.

        Raises KeyError if object_type is not registered.
        """
        with self._lock:
            adapter = self._adapters[object_type]
        return adapter.serialize(obj)

    def deserialize(self, object_type: str, data: dict[str, Any]) -> Any:
        """Deserialize a dict to a typed instance.

        Returns the raw dict if object_type is not registered.
        """
        with self._lock:
            cls = self._types.get(object_type)
            adapter = self._adapters.get(object_type)
        if cls is None or adapter is None:
            return data
        return adapter.deserialize(cls, data)

    def clear(self) -> None:
        """Remove all registrations.  For testing only."""
        with self._lock:
            self._types.clear()
            self._adapters.clear()


# Module-level singleton
_registry = TypeRegistry()
