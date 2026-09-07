"""Registry introspection — discover constructs and check completeness.

Scans registered constructs and validates that the registry is consistent
and complete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scoped.registry.base import Registry, RegistryEntry
from scoped.storage.interface import StorageBackend
from scoped.types import Lifecycle


@dataclass(frozen=True, slots=True)
class IntrospectionResult:
    """Result of a registry introspection scan."""

    total_entries: int
    active_entries: int
    archived_entries: int
    by_kind: dict[str, int]
    by_namespace: dict[str, int]
    by_lifecycle: dict[str, int]
    orphaned_entries: tuple[str, ...]
    """Entry IDs that reference non-existent targets."""
    duplicate_urns: tuple[str, ...]
    """URNs that appear more than once."""

    @property
    def is_clean(self) -> bool:
        """True if no issues found."""
        return len(self.orphaned_entries) == 0 and len(self.duplicate_urns) == 0


class RegistryIntrospector:
    """Analyze a registry for completeness and consistency."""

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def scan(self) -> IntrospectionResult:
        """Perform a full scan of the registry.

        Checks:
        - Count entries by kind, namespace, and lifecycle state
        - Detect duplicate URNs
        - Detect orphaned entries (entries with broken FK references)
        """
        rows = self._backend.fetch_all(
            "SELECT * FROM registry_entries ORDER BY registered_at",
            (),
        )

        by_kind: dict[str, int] = {}
        by_namespace: dict[str, int] = {}
        by_lifecycle: dict[str, int] = {}
        urn_counts: dict[str, int] = {}
        active = 0
        archived = 0

        for row in rows:
            kind = row["kind"]
            namespace = row["namespace"]
            lifecycle = row.get("lifecycle", "ACTIVE")
            urn = row["urn"]

            by_kind[kind] = by_kind.get(kind, 0) + 1
            by_namespace[namespace] = by_namespace.get(namespace, 0) + 1
            by_lifecycle[lifecycle] = by_lifecycle.get(lifecycle, 0) + 1
            urn_counts[urn] = urn_counts.get(urn, 0) + 1

            if lifecycle == "ACTIVE":
                active += 1
            elif lifecycle == "ARCHIVED":
                archived += 1

        duplicate_urns = tuple(u for u, c in urn_counts.items() if c > 1)

        # Check for orphaned principal references
        orphaned = self._find_orphaned_principals()

        return IntrospectionResult(
            total_entries=len(rows),
            active_entries=active,
            archived_entries=archived,
            by_kind=by_kind,
            by_namespace=by_namespace,
            by_lifecycle=by_lifecycle,
            orphaned_entries=orphaned,
            duplicate_urns=duplicate_urns,
        )

    def check_table_registered(
        self,
        table_name: str,
        *,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        """Check if rows in a given table have corresponding registry entries.

        Returns rows that are missing registry entries.
        """
        rows = self._backend.fetch_all(
            f"SELECT id FROM {table_name}",
            (),
        )
        all_ids = {r["id"] for r in rows}

        # Get all registered entry IDs
        reg_rows = self._backend.fetch_all(
            "SELECT id FROM registry_entries",
            (),
        )
        registered_ids = {r["id"] for r in reg_rows}

        missing = all_ids - registered_ids
        return [{"id": m, "table": table_name} for m in missing]

    def validate_lifecycle_consistency(self) -> list[dict[str, Any]]:
        """Check that lifecycle states are valid across registry entries."""
        valid_states = {s.name for s in Lifecycle}
        rows = self._backend.fetch_all(
            "SELECT id, urn, lifecycle FROM registry_entries",
            (),
        )
        invalid = []
        for row in rows:
            if row.get("lifecycle") not in valid_states:
                invalid.append({
                    "id": row["id"],
                    "urn": row["urn"],
                    "lifecycle": row.get("lifecycle"),
                })
        return invalid

    def _find_orphaned_principals(self) -> tuple[str, ...]:
        """Find principals whose registry_entry_id doesn't exist."""
        rows = self._backend.fetch_all(
            "SELECT p.id FROM principals p "
            "LEFT JOIN registry_entries re ON p.registry_entry_id = re.id "
            "WHERE re.id IS NULL",
            (),
        )
        return tuple(r["id"] for r in rows)

    def _find_orphaned_entities(
        self,
        table: str,
        fk_column: str,
        ref_table: str,
    ) -> tuple[str, ...]:
        """Generic orphan detection: find rows whose FK doesn't exist."""
        try:
            rows = self._backend.fetch_all(
                f"SELECT t.id FROM {table} t "
                f"LEFT JOIN {ref_table} r ON t.{fk_column} = r.id "
                f"WHERE r.id IS NULL",
                (),
            )
            return tuple(r["id"] for r in rows)
        except Exception:
            return ()

    def check_all_tables_registered(self) -> dict[str, list[dict[str, Any]]]:
        """Check registry completeness across all entity types with registry_entry_id.

        Returns a dict of table_name -> list of unregistered row dicts.
        Only checks tables that have a registry_entry_id column.
        """
        # Tables with registry_entry_id FK
        fk_tables = ["principals", "scoped_objects"]

        results: dict[str, list[dict[str, Any]]] = {}
        for table in fk_tables:
            try:
                orphaned = self._backend.fetch_all(
                    f"SELECT t.id FROM {table} t "
                    f"LEFT JOIN registry_entries re ON t.registry_entry_id = re.id "
                    f"WHERE t.registry_entry_id IS NOT NULL AND re.id IS NULL",
                    (),
                )
                if orphaned:
                    results[table] = [{"id": r["id"], "table": table} for r in orphaned]
            except Exception:
                pass

        return results
