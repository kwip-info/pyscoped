"""Persistent storage for registry entries.

The in-memory Registry is the primary interface. The RegistryStore provides
persistence — saving/loading entries to a backend so the registry survives
restarts.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from scoped.registry.base import Registry, RegistryEntry
from scoped.registry.kinds import CustomKind, RegistryKind
from scoped.types import Lifecycle, Metadata, URN, now_utc


class RegistryStore(ABC):
    """Abstract interface for persisting registry entries."""

    @abstractmethod
    def save_entry(self, entry: RegistryEntry) -> None:
        """Persist a single registry entry."""
        ...

    @abstractmethod
    def save_all(self, entries: list[RegistryEntry]) -> None:
        """Persist all entries (bulk)."""
        ...

    @abstractmethod
    def load_all(self) -> list[dict[str, Any]]:
        """Load all persisted entries as raw dicts."""
        ...

    @abstractmethod
    def delete_entry(self, entry_id: str) -> None:
        """Remove a persisted entry by ID."""
        ...

    @abstractmethod
    def clear(self) -> None:
        """Remove all persisted entries."""
        ...

    def hydrate_registry(self, registry: Registry) -> int:
        """
        Load persisted entries into a Registry instance.

        Returns the number of entries loaded.
        """
        raw_entries = self.load_all()
        count = 0
        for raw in raw_entries:
            _hydrate_entry(registry, raw)
            count += 1
        return count

    def persist_registry(self, registry: Registry) -> int:
        """
        Save all entries from a Registry to the store.

        Returns the number of entries saved.
        """
        entries = registry.all()
        self.save_all(entries)
        return len(entries)


def _resolve_kind(kind_name: str) -> RegistryKind | CustomKind:
    """Resolve a kind name string back to enum/custom kind."""
    try:
        return RegistryKind[kind_name]
    except KeyError:
        custom = CustomKind.get(kind_name)
        if custom:
            return custom
        # Create it on the fly — it was registered at some point
        return CustomKind.define(kind_name)


def _hydrate_entry(registry: Registry, raw: dict[str, Any]) -> RegistryEntry:
    """Reconstruct a RegistryEntry from a raw dict and index it directly.

    Unlike ``register()``, this preserves the stored ID and timestamps so that
    the in-memory registry stays in sync with the database.
    """
    from datetime import datetime

    urn = URN.parse(raw["urn"])
    kind = _resolve_kind(raw["kind"])
    lifecycle = Lifecycle[raw["lifecycle"]]

    registered_at = raw.get("registered_at")
    if isinstance(registered_at, str):
        # Handle ISO format timestamps from DB
        registered_at = datetime.fromisoformat(registered_at)
    if registered_at is None:
        registered_at = now_utc()

    entry = RegistryEntry(
        id=raw["id"],
        urn=urn,
        kind=kind,
        lifecycle=lifecycle,
        registered_at=registered_at,
        registered_by=raw.get("registered_by", "system"),
        target=None,
        metadata=Metadata(data=raw.get("metadata", {})),
        namespace=raw["namespace"],
        tags=set(raw.get("tags", [])),
        entry_version=raw.get("entry_version", 1),
    )
    entry.previous_entry_id = raw.get("previous_entry_id")
    registry._index_entry(entry)
    return entry


# ---------------------------------------------------------------------------
# In-memory store (for testing)
# ---------------------------------------------------------------------------

class InMemoryRegistryStore(RegistryStore):
    """Simple in-memory store. Useful for tests."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def save_entry(self, entry: RegistryEntry) -> None:
        self._data[entry.id] = entry.snapshot()

    def save_all(self, entries: list[RegistryEntry]) -> None:
        for entry in entries:
            self.save_entry(entry)

    def load_all(self) -> list[dict[str, Any]]:
        return list(self._data.values())

    def delete_entry(self, entry_id: str) -> None:
        self._data.pop(entry_id, None)

    def clear(self) -> None:
        self._data.clear()
