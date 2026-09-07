"""PrincipalResolver — walk the principal relationship graph.

Provides graph traversal utilities: ancestors, descendants, related
principals, path-finding, and relationship-type filtering.  The graph
shape is application-defined; the resolver just walks it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from scoped.identity.principal import Principal, PrincipalRelationship, PrincipalStore


@dataclass(frozen=True, slots=True)
class ResolutionPath:
    """A path through the principal graph from start to end."""
    principals: tuple[str, ...]          # ordered principal IDs
    relationships: tuple[str, ...]       # edge labels along the path

    @property
    def length(self) -> int:
        return len(self.relationships)


class PrincipalResolver:
    """
    Graph walker for principal relationships.

    All traversals are bounded by ``max_depth`` to prevent runaway walks
    on cyclic or very deep graphs.
    """

    DEFAULT_MAX_DEPTH = 20

    def __init__(self, store: PrincipalStore) -> None:
        self._store = store

    # -- Ancestors (walk parent edges upward) --------------------------------

    def ancestors(
        self,
        principal_id: str,
        *,
        relationship: str | None = None,
        max_depth: int | None = None,
    ) -> list[Principal]:
        """
        All principals reachable by walking *parent* edges from ``principal_id``.

        If ``relationship`` is given, only edges with that label are followed.
        """
        depth = max_depth or self.DEFAULT_MAX_DEPTH
        visited: set[str] = set()
        result: list[Principal] = []
        queue: deque[tuple[str, int]] = deque([(principal_id, 0)])

        while queue:
            pid, d = queue.popleft()
            if d >= depth:
                continue
            rels = self._store.get_relationships(
                pid, direction="parent", relationship=relationship,
            )
            for rel in rels:
                if rel.parent_id not in visited:
                    visited.add(rel.parent_id)
                    parent = self._store.find_principal(rel.parent_id)
                    if parent is not None:
                        result.append(parent)
                        queue.append((rel.parent_id, d + 1))

        return result

    # -- Descendants (walk child edges downward) -----------------------------

    def descendants(
        self,
        principal_id: str,
        *,
        relationship: str | None = None,
        max_depth: int | None = None,
    ) -> list[Principal]:
        """
        All principals reachable by walking *child* edges from ``principal_id``.
        """
        depth = max_depth or self.DEFAULT_MAX_DEPTH
        visited: set[str] = set()
        result: list[Principal] = []
        queue: deque[tuple[str, int]] = deque([(principal_id, 0)])

        while queue:
            pid, d = queue.popleft()
            if d >= depth:
                continue
            rels = self._store.get_relationships(
                pid, direction="child", relationship=relationship,
            )
            for rel in rels:
                if rel.child_id not in visited:
                    visited.add(rel.child_id)
                    child = self._store.find_principal(rel.child_id)
                    if child is not None:
                        result.append(child)
                        queue.append((rel.child_id, d + 1))

        return result

    # -- Direct relatives ---------------------------------------------------

    def parents(
        self,
        principal_id: str,
        *,
        relationship: str | None = None,
    ) -> list[Principal]:
        """Immediate parents (depth=1 ancestors)."""
        return self.ancestors(
            principal_id, relationship=relationship, max_depth=1,
        )

    def children(
        self,
        principal_id: str,
        *,
        relationship: str | None = None,
    ) -> list[Principal]:
        """Immediate children (depth=1 descendants)."""
        return self.descendants(
            principal_id, relationship=relationship, max_depth=1,
        )

    # -- Path finding -------------------------------------------------------

    def find_path(
        self,
        from_id: str,
        to_id: str,
        *,
        relationship: str | None = None,
        max_depth: int | None = None,
    ) -> ResolutionPath | None:
        """
        Find a path from ``from_id`` to ``to_id`` by walking parent edges.

        Returns ``None`` if no path exists within ``max_depth``.
        """
        depth = max_depth or self.DEFAULT_MAX_DEPTH

        # BFS with path tracking
        visited: set[str] = {from_id}
        # Each queue entry: (current_id, path_of_ids, path_of_relationships)
        queue: deque[tuple[str, list[str], list[str]]] = deque([
            (from_id, [from_id], [])
        ])

        while queue:
            current, path_ids, path_rels = queue.popleft()
            if len(path_rels) > depth:
                continue

            # Walk both directions to find any connection
            rels = self._store.get_relationships(
                current, direction="both", relationship=relationship,
            )
            for rel in rels:
                neighbor = (
                    rel.parent_id if rel.child_id == current else rel.child_id
                )
                if neighbor == to_id:
                    return ResolutionPath(
                        principals=tuple(path_ids + [neighbor]),
                        relationships=tuple(path_rels + [rel.relationship]),
                    )
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((
                        neighbor,
                        path_ids + [neighbor],
                        path_rels + [rel.relationship],
                    ))

        return None

    # -- Membership check ---------------------------------------------------

    def is_related(
        self,
        principal_id: str,
        target_id: str,
        *,
        relationship: str | None = None,
        max_depth: int | None = None,
    ) -> bool:
        """Check whether ``principal_id`` can reach ``target_id`` in the graph."""
        return self.find_path(
            principal_id, target_id,
            relationship=relationship,
            max_depth=max_depth,
        ) is not None

    # -- Collect all related IDs (useful for scope resolution) ---------------

    def all_related_ids(
        self,
        principal_id: str,
        *,
        relationship: str | None = None,
        max_depth: int | None = None,
    ) -> set[str]:
        """
        Return the IDs of all principals reachable from ``principal_id``
        (both ancestors and descendants).  Includes ``principal_id`` itself.
        """
        ancestors = {p.id for p in self.ancestors(
            principal_id, relationship=relationship, max_depth=max_depth,
        )}
        descendants = {p.id for p in self.descendants(
            principal_id, relationship=relationship, max_depth=max_depth,
        )}
        return {principal_id} | ancestors | descendants
