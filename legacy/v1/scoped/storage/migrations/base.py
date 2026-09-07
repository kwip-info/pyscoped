"""Base migration interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scoped.storage.interface import StorageBackend


class BaseMigration(ABC):
    """A single schema migration.

    Subclasses declare a version number and name, then implement up/down
    to apply/reverse the schema change.
    """

    @property
    @abstractmethod
    def version(self) -> int:
        """Unique, monotonically increasing migration number."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable description of the migration."""
        ...

    @abstractmethod
    def up(self, backend: StorageBackend) -> None:
        """Apply the migration (forward)."""
        ...

    @abstractmethod
    def down(self, backend: StorageBackend) -> None:
        """Reverse the migration (rollback)."""
        ...

    def __repr__(self) -> str:
        return f"Migration({self.version:04d}_{self.name})"
