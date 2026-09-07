"""Name-to-ID reference resolution during manifest loading."""

from __future__ import annotations

from scoped.manifest.exceptions import ManifestLoadError


class ReferenceResolver:
    """Maps (section, name) -> entity ID during manifest loading.

    Two-phase usage:
      1. Validator builds the name graph to catch dangling refs.
      2. Loader registers IDs as entities are created, then resolves refs.
    """

    def __init__(self) -> None:
        self._map: dict[tuple[str, str], str] = {}

    def register(self, section: str, name: str, entity_id: str) -> None:
        """Register a created entity's ID."""
        self._map[(section, name)] = entity_id

    def resolve(self, section: str, name: str) -> str:
        """Resolve a name reference to its entity ID.

        Raises ManifestLoadError if the reference hasn't been registered.
        """
        key = (section, name)
        if key not in self._map:
            raise ManifestLoadError(
                f"Unresolved reference: {section}/{name} — "
                f"entity not yet created or missing from manifest"
            )
        return self._map[key]

    def has(self, section: str, name: str) -> bool:
        """Check if a reference is registered."""
        return (section, name) in self._map

    @property
    def entries(self) -> dict[tuple[str, str], str]:
        """All registered entries."""
        return dict(self._map)
