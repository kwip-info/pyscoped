"""Core registry: RegistryEntry and Registry.

The Registry is the single source of truth for what exists in the system.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterator

from scoped.exceptions import (
    AlreadyRegisteredError,
    NotRegisteredError,
    RegistryFrozenError,
)
from scoped.registry.kinds import CustomKind, RegistryKind
from scoped.types import Lifecycle, Metadata, URN, generate_id, now_utc


# ---------------------------------------------------------------------------
# RegistryEntry — one entry per registered construct
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RegistryEntry:
    """
    A single entry in the universal registry.

    Every model, function, class, relationship, view, or custom construct
    that participates in the framework gets one of these.
    """

    id: str
    urn: URN
    kind: RegistryKind | CustomKind
    lifecycle: Lifecycle
    registered_at: datetime
    registered_by: str          # principal id of who registered it (or "system")
    target: Any                 # the actual object (class, function, etc.) — may be None for data
    metadata: Metadata
    namespace: str              # logical grouping (app name, module, etc.)
    tags: set[str] = field(default_factory=set)

    # Version tracking for the entry itself (not the target)
    entry_version: int = 1
    previous_entry_id: str | None = None

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, RegistryEntry):
            return self.id == other.id
        return NotImplemented

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    def snapshot(self) -> dict[str, Any]:
        """Serializable snapshot of this entry for audit/versioning."""
        return {
            "id": self.id,
            "urn": str(self.urn),
            "kind": self.kind.name if isinstance(self.kind, RegistryKind) else self.kind.name,
            "lifecycle": self.lifecycle.name,
            "registered_at": self.registered_at.isoformat(),
            "registered_by": self.registered_by,
            "namespace": self.namespace,
            "tags": sorted(self.tags),
            "entry_version": self.entry_version,
            "metadata": self.metadata.snapshot(),
        }


# ---------------------------------------------------------------------------
# Registry — the global construct registry
# ---------------------------------------------------------------------------

class Registry:
    """
    The Universal Registry.

    Thread-safe. Supports lookup by id, URN, kind, namespace, tags.
    Can be frozen to prevent further modifications (e.g., after app startup).

    The registry is NOT a singleton by design — tests can create isolated
    registries. For production use, `scoped.registry.get_registry()` returns
    the global instance.
    """

    def __init__(self) -> None:
        self._entries: dict[str, RegistryEntry] = {}       # id -> entry
        self._by_urn: dict[str, RegistryEntry] = {}        # str(urn) -> entry
        self._by_kind: dict[str, list[str]] = {}           # kind_name -> [entry_ids]
        self._by_namespace: dict[str, list[str]] = {}      # namespace -> [entry_ids]
        self._by_target: dict[int, str] = {}               # id(target) -> entry_id
        self._lock = threading.RLock()
        self._frozen = False
        self._listeners: list[Callable[[str, RegistryEntry], None]] = []

    # -- State --

    @property
    def frozen(self) -> bool:
        return self._frozen

    def freeze(self) -> None:
        """Prevent further registrations. Typically called after app init."""
        self._frozen = True

    def unfreeze(self) -> None:
        """Re-allow registrations. Primarily for testing."""
        self._frozen = False

    # -- Listeners --

    def on_change(self, callback: Callable[[str, RegistryEntry], None]) -> None:
        """
        Register a listener called on every registry mutation.

        Callback receives (event_type, entry) where event_type is one of:
        "register", "update", "lifecycle_change".
        """
        with self._lock:
            self._listeners.append(callback)

    def _notify(self, event: str, entry: RegistryEntry) -> None:
        """Notify listeners. Must be called OUTSIDE ``self._lock``."""
        with self._lock:
            listeners = list(self._listeners)
        for cb in listeners:
            cb(event, entry)

    # -- Registration --

    def register(
        self,
        *,
        kind: RegistryKind | CustomKind,
        namespace: str,
        name: str,
        registered_by: str = "system",
        target: Any = None,
        metadata: dict[str, Any] | None = None,
        tags: set[str] | None = None,
        version: int = 1,
        lifecycle: Lifecycle = Lifecycle.ACTIVE,
    ) -> RegistryEntry:
        """
        Register a construct in the registry.

        Raises AlreadyRegisteredError if a URN collision occurs.
        Raises RegistryFrozenError if the registry is frozen.
        """
        with self._lock:
            if self._frozen:
                raise RegistryFrozenError(
                    "Registry is frozen — cannot register new constructs",
                    context={"kind": str(kind), "namespace": namespace, "name": name},
                )

            urn = URN(
                kind=kind.name if isinstance(kind, RegistryKind) else kind.name,
                namespace=namespace,
                name=name,
                version=version,
            )
            urn_str = str(urn)

            if urn_str in self._by_urn:
                raise AlreadyRegisteredError(
                    f"Construct already registered: {urn_str}",
                    context={"urn": urn_str},
                )

            entry = RegistryEntry(
                id=generate_id(),
                urn=urn,
                kind=kind,
                lifecycle=lifecycle,
                registered_at=now_utc(),
                registered_by=registered_by,
                target=target,
                metadata=Metadata(data=metadata or {}),
                namespace=namespace,
                tags=tags or set(),
            )

            self._index_entry(entry)

        self._notify("register", entry)
        return entry

    def _index_entry(self, entry: RegistryEntry) -> None:
        """Add entry to all lookup indexes (idempotent)."""
        self._entries[entry.id] = entry
        self._by_urn[str(entry.urn)] = entry

        kind_name = entry.kind.name if isinstance(entry.kind, RegistryKind) else entry.kind.name
        kind_list = self._by_kind.setdefault(kind_name, [])
        if entry.id not in kind_list:
            kind_list.append(entry.id)
        ns_list = self._by_namespace.setdefault(entry.namespace, [])
        if entry.id not in ns_list:
            ns_list.append(entry.id)

        if entry.target is not None:
            self._by_target[id(entry.target)] = entry.id

    # -- Lookup --

    def get(self, entry_id: str) -> RegistryEntry:
        """Get entry by ID. Raises NotRegisteredError if not found."""
        with self._lock:
            try:
                return self._entries[entry_id]
            except KeyError:
                raise NotRegisteredError(
                    f"No registry entry with id: {entry_id}",
                    context={"id": entry_id},
                )

    def get_by_urn(self, urn: URN | str) -> RegistryEntry:
        """Get entry by URN. Raises NotRegisteredError if not found."""
        urn_str = str(urn)
        with self._lock:
            try:
                return self._by_urn[urn_str]
            except KeyError:
                raise NotRegisteredError(
                    f"No registry entry with URN: {urn_str}",
                    context={"urn": urn_str},
                )

    def get_by_target(self, target: Any) -> RegistryEntry:
        """Get entry by the actual target object (class, function, etc.)."""
        with self._lock:
            target_id = id(target)
            if target_id not in self._by_target:
                raise NotRegisteredError(
                    f"Target object is not registered: {target!r}",
                    context={"target_repr": repr(target)},
                )
            return self._entries[self._by_target[target_id]]

    def find_by_urn(self, urn: URN | str) -> RegistryEntry | None:
        """Like get_by_urn but returns None instead of raising."""
        with self._lock:
            return self._by_urn.get(str(urn))

    def find_by_target(self, target: Any) -> RegistryEntry | None:
        """Like get_by_target but returns None instead of raising."""
        with self._lock:
            entry_id = self._by_target.get(id(target))
            return self._entries.get(entry_id) if entry_id else None

    # -- Query --

    def by_kind(self, kind: RegistryKind | CustomKind) -> list[RegistryEntry]:
        """Get all entries of a given kind."""
        with self._lock:
            kind_name = kind.name if isinstance(kind, RegistryKind) else kind.name
            entry_ids = self._by_kind.get(kind_name, [])
            return [self._entries[eid] for eid in entry_ids]

    def by_namespace(self, namespace: str) -> list[RegistryEntry]:
        """Get all entries in a namespace."""
        with self._lock:
            entry_ids = self._by_namespace.get(namespace, [])
            return [self._entries[eid] for eid in entry_ids]

    def by_tag(self, tag: str) -> list[RegistryEntry]:
        """Get all entries that have a specific tag."""
        with self._lock:
            return [e for e in self._entries.values() if tag in e.tags]

    def by_lifecycle(self, lifecycle: Lifecycle) -> list[RegistryEntry]:
        """Get all entries in a specific lifecycle state."""
        with self._lock:
            return [e for e in self._entries.values() if e.lifecycle == lifecycle]

    def query(
        self,
        *,
        kind: RegistryKind | CustomKind | None = None,
        namespace: str | None = None,
        tag: str | None = None,
        lifecycle: Lifecycle | None = None,
        predicate: Callable[[RegistryEntry], bool] | None = None,
    ) -> list[RegistryEntry]:
        """Flexible query with multiple optional filters."""
        with self._lock:
            results: Iterator[RegistryEntry] = iter(self._entries.values())

            if kind is not None:
                kind_name = kind.name
                results = (e for e in results if (
                    e.kind.name if isinstance(e.kind, RegistryKind) else e.kind.name
                ) == kind_name)

            if namespace is not None:
                results = (e for e in results if e.namespace == namespace)

            if tag is not None:
                results = (e for e in results if tag in e.tags)

            if lifecycle is not None:
                results = (e for e in results if e.lifecycle == lifecycle)

            if predicate is not None:
                results = (e for e in results if predicate(e))

            return list(results)

    # -- Lifecycle transitions --

    def transition(self, entry_id: str, new_lifecycle: Lifecycle) -> RegistryEntry:
        """
        Transition an entry to a new lifecycle state.

        Creates a new version of the entry (the old version is preserved
        via previous_entry_id linkage).
        """
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry is None:
                raise NotRegisteredError(
                    f"No registry entry with id: {entry_id}",
                    context={"id": entry_id},
                )
            entry.previous_entry_id = None
            old_version = entry.entry_version

            entry.lifecycle = new_lifecycle
            entry.entry_version = old_version + 1

        self._notify("lifecycle_change", entry)
        return entry

    # -- Bulk operations --

    def all(self) -> list[RegistryEntry]:
        """Return all entries."""
        with self._lock:
            return list(self._entries.values())

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def contains_urn(self, urn: URN | str) -> bool:
        with self._lock:
            return str(urn) in self._by_urn

    def contains_target(self, target: Any) -> bool:
        with self._lock:
            return id(target) in self._by_target

    # -- Removal (soft — transitions to ARCHIVED) --

    def archive(self, entry_id: str) -> RegistryEntry:
        """Archive an entry. It remains in the registry but is no longer active.

        Frees the URN slot and removes from kind/namespace indexes so the
        entry doesn't appear in active queries or block new registrations.
        """
        with self._lock:
            entry = self._entries.get(entry_id)
            if entry is None:
                raise NotRegisteredError(
                    f"No registry entry with id: {entry_id}",
                    context={"id": entry_id},
                )
            entry.previous_entry_id = None
            old_version = entry.entry_version
            entry.lifecycle = Lifecycle.ARCHIVED
            entry.entry_version = old_version + 1

            # Free URN slot
            self._by_urn.pop(str(entry.urn), None)
            # Remove from kind and namespace indexes
            kind_name = entry.kind.name if isinstance(entry.kind, RegistryKind) else entry.kind.name
            kind_list = self._by_kind.get(kind_name, [])
            self._by_kind[kind_name] = [eid for eid in kind_list if eid != entry_id]
            ns_list = self._by_namespace.get(entry.namespace, [])
            self._by_namespace[entry.namespace] = [eid for eid in ns_list if eid != entry_id]

        self._notify("lifecycle_change", entry)
        return entry

    # -- Reset (testing only) --

    def clear(self) -> None:
        """Remove all entries. For testing only."""
        with self._lock:
            self._entries.clear()
            self._by_urn.clear()
            self._by_kind.clear()
            self._by_namespace.clear()
            self._by_target.clear()
            self._frozen = False


# ---------------------------------------------------------------------------
# Global registry instance
# ---------------------------------------------------------------------------

_global_registry: Registry | None = None


def get_registry() -> Registry:
    """Get or create the global registry instance."""
    global _global_registry
    if _global_registry is None:
        _global_registry = Registry()
    return _global_registry


def reset_global_registry() -> None:
    """Reset the global registry. For testing only."""
    global _global_registry
    if _global_registry is not None:
        _global_registry.clear()
    _global_registry = None
