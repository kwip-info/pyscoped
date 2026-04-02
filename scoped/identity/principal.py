"""Principal — the generic identity primitive.

Principals are any registered entity that can act. The framework provides
the machinery; the application defines the kinds (user, team, bot, org, etc.).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from scoped.exceptions import (
    IdentityError,
    PrincipalNotFoundError,
)
from scoped.registry.base import Registry, RegistryEntry, get_registry
from scoped.registry.kinds import RegistryKind
from scoped.registry.sqlite_store import SQLiteRegistryStore
from scoped.storage._query import compile_for
from scoped.storage._schema import principal_relationships, principals
from scoped.ids import PrincipalId
from scoped.types import ActionType, Lifecycle, Metadata, generate_id, now_utc


# ---------------------------------------------------------------------------
# Principal — a registered entity that can act
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Principal:
    """
    Any entity that can perform actions in the system.

    The ``kind`` field is application-defined — the framework does not
    prescribe what kinds of principals exist.  A User, a Bot, a Team,
    a ServiceAccount are all just principals with different kinds.
    """

    id: str
    kind: str                       # application-defined: "user", "team", "org", …
    display_name: str
    registry_entry_id: str          # link to universal registry
    created_at: datetime
    created_by: str                 # principal id who created this (or "system")
    lifecycle: Lifecycle = Lifecycle.ACTIVE
    metadata: Metadata = field(default_factory=Metadata)

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    def snapshot(self) -> dict[str, Any]:
        """Serializable snapshot for audit/versioning."""
        return {
            "id": self.id,
            "kind": self.kind,
            "display_name": self.display_name,
            "registry_entry_id": self.registry_entry_id,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "lifecycle": self.lifecycle.name,
            "metadata": self.metadata.snapshot(),
        }


# ---------------------------------------------------------------------------
# PrincipalRelationship — directed edge between principals
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PrincipalRelationship:
    """
    A directed edge in the principal graph.

    The ``relationship`` label is application-defined: "member_of", "owns",
    "administers", etc.  The framework walks these edges but does not
    prescribe the graph shape.
    """

    id: str
    parent_id: str
    child_id: str
    relationship: str               # application-defined label
    created_at: datetime
    created_by: str
    metadata: Metadata = field(default_factory=Metadata)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "child_id": self.child_id,
            "relationship": self.relationship,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "metadata": self.metadata.snapshot(),
        }


# ---------------------------------------------------------------------------
# PrincipalStore — CRUD for principals against a StorageBackend
# ---------------------------------------------------------------------------

class PrincipalStore:
    """
    Persistence layer for principals and their relationships.

    Operates against a ``StorageBackend`` (the same one used for registry,
    objects, etc.).  All writes go through the storage transaction model.
    """

    def __init__(self, backend: Any, *, audit_writer: Any | None = None) -> None:
        from scoped.storage.interface import StorageBackend
        if not isinstance(backend, StorageBackend):
            raise TypeError(f"Expected StorageBackend, got {type(backend).__name__}")
        self._backend = backend
        self._audit = audit_writer

    # -- Principal CRUD -----------------------------------------------------

    def create_principal(
        self,
        *,
        kind: str,
        display_name: str = "",
        created_by: str = "system",
        metadata: dict[str, Any] | None = None,
        registry: Registry | None = None,
        principal_id: str | None = None,
    ) -> Principal:
        """Create a new principal, register it, and persist it."""
        reg = registry or get_registry()
        pid = principal_id or PrincipalId.generate()
        ts = now_utc()

        # Register in the universal registry
        entry: RegistryEntry = reg.register(
            kind=RegistryKind.PRINCIPAL,
            namespace="identity",
            name=f"{kind}:{pid}",
            registered_by=created_by,
            metadata={"principal_id": pid, "principal_kind": kind},
        )

        # Persist registry entry to storage so FK constraints are satisfied
        reg_store = SQLiteRegistryStore(self._backend)
        reg_store.save_entry(entry)

        principal = Principal(
            id=pid,
            kind=kind,
            display_name=display_name,
            registry_entry_id=entry.id,
            created_at=ts,
            created_by=created_by,
            metadata=Metadata(data=metadata or {}),
        )

        stmt = sa.insert(principals).values(
            id=principal.id,
            kind=principal.kind,
            display_name=principal.display_name,
            registry_entry_id=principal.registry_entry_id,
            created_at=principal.created_at.isoformat(),
            created_by=principal.created_by,
            lifecycle=principal.lifecycle.name,
            metadata_json=json.dumps(principal.metadata.snapshot()),
        )
        sql, params = compile_for(stmt, self._backend.dialect)

        with self._backend.transaction() as txn:
            txn.execute(sql, params)
            txn.commit()

        if self._audit is not None:
            try:
                self._audit.record(
                    actor_id=created_by,
                    action=ActionType.CREATE,
                    target_type="principal",
                    target_id=principal.id,
                    after_state={"kind": kind, "display_name": display_name},
                )
            except Exception:
                pass  # audit failure must not block principal creation

        return principal

    def get_principal(self, principal_id: str) -> Principal:
        """Fetch a principal by ID.  Raises PrincipalNotFoundError if missing."""
        stmt = sa.select(principals).where(principals.c.id == principal_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            raise PrincipalNotFoundError(
                f"Principal not found: {principal_id}",
                context={"principal_id": principal_id},
            )
        return self._row_to_principal(row)

    def find_principal(self, principal_id: str) -> Principal | None:
        """Like get_principal but returns None instead of raising."""
        stmt = sa.select(principals).where(principals.c.id == principal_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return self._row_to_principal(row) if row else None

    def list_principals(
        self,
        *,
        kind: str | None = None,
        lifecycle: Lifecycle | None = None,
    ) -> list[Principal]:
        """List principals with optional filters."""
        stmt = sa.select(principals)

        if kind is not None:
            stmt = stmt.where(principals.c.kind == kind)
        if lifecycle is not None:
            stmt = stmt.where(principals.c.lifecycle == lifecycle.name)

        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [self._row_to_principal(r) for r in rows]

    def update_lifecycle(self, principal_id: str, new_lifecycle: Lifecycle) -> Principal:
        """Transition a principal's lifecycle state."""
        principal = self.get_principal(principal_id)
        old_lifecycle = principal.lifecycle.name

        stmt = sa.update(principals).where(principals.c.id == principal_id).values(
            lifecycle=new_lifecycle.name,
        )
        sql, params = compile_for(stmt, self._backend.dialect)

        with self._backend.transaction() as txn:
            txn.execute(sql, params)
            txn.commit()
        principal.lifecycle = new_lifecycle

        if self._audit is not None:
            try:
                self._audit.record(
                    actor_id="system",
                    action=ActionType.LIFECYCLE_CHANGE,
                    target_type="principal",
                    target_id=principal_id,
                    before_state={"lifecycle": old_lifecycle},
                    after_state={"lifecycle": new_lifecycle.name},
                )
            except Exception:
                pass

        return principal

    def update_principal(
        self,
        principal_id: str,
        *,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        updated_by: str = "system",
    ) -> Principal:
        """Update a principal's display_name and/or metadata."""
        principal = self.get_principal(principal_id)
        before = {}
        after = {}

        values: dict[str, Any] = {}

        if display_name is not None and display_name != principal.display_name:
            values["display_name"] = display_name
            before["display_name"] = principal.display_name
            after["display_name"] = display_name

        if metadata is not None:
            merged = {**principal.metadata.snapshot(), **metadata}
            values["metadata_json"] = json.dumps(merged)
            before["metadata"] = principal.metadata.snapshot()
            after["metadata"] = merged

        if not values:
            return principal

        stmt = sa.update(principals).where(principals.c.id == principal_id).values(**values)
        sql, params = compile_for(stmt, self._backend.dialect)

        with self._backend.transaction() as txn:
            txn.execute(sql, params)
            txn.commit()

        if self._audit is not None:
            try:
                self._audit.record(
                    actor_id=updated_by,
                    action=ActionType.UPDATE,
                    target_type="principal",
                    target_id=principal_id,
                    before_state=before,
                    after_state=after,
                )
            except Exception:
                pass

        return self.get_principal(principal_id)

    # -- Relationship CRUD --------------------------------------------------

    def add_relationship(
        self,
        *,
        parent_id: str,
        child_id: str,
        relationship: str = "member_of",
        created_by: str = "system",
        metadata: dict[str, Any] | None = None,
    ) -> PrincipalRelationship:
        """Create a directed relationship between two principals."""
        # Verify both principals exist
        self.get_principal(parent_id)
        self.get_principal(child_id)

        rel = PrincipalRelationship(
            id=generate_id(),
            parent_id=parent_id,
            child_id=child_id,
            relationship=relationship,
            created_at=now_utc(),
            created_by=created_by,
            metadata=Metadata(data=metadata or {}),
        )

        stmt = sa.insert(principal_relationships).values(
            id=rel.id,
            parent_id=rel.parent_id,
            child_id=rel.child_id,
            relationship=rel.relationship,
            created_at=rel.created_at.isoformat(),
            created_by=rel.created_by,
            metadata_json=json.dumps(rel.metadata.snapshot()),
        )
        sql, params = compile_for(stmt, self._backend.dialect)

        with self._backend.transaction() as txn:
            txn.execute(sql, params)
            txn.commit()

        return rel

    def remove_relationship(self, relationship_id: str) -> None:
        """Remove a relationship by ID."""
        stmt = sa.delete(principal_relationships).where(
            principal_relationships.c.id == relationship_id
        )
        sql, params = compile_for(stmt, self._backend.dialect)

        with self._backend.transaction() as txn:
            txn.execute(sql, params)
            txn.commit()

    def get_relationships(
        self,
        principal_id: str,
        *,
        direction: str = "both",
        relationship: str | None = None,
    ) -> list[PrincipalRelationship]:
        """
        Get relationships for a principal.

        direction: "parent" (where principal is child), "child" (where principal
        is parent), or "both".
        """
        results: list[PrincipalRelationship] = []

        if direction in ("parent", "both"):
            stmt = sa.select(principal_relationships).where(
                principal_relationships.c.child_id == principal_id
            )
            if relationship:
                stmt = stmt.where(principal_relationships.c.relationship == relationship)
            sql, params = compile_for(stmt, self._backend.dialect)
            rows = self._backend.fetch_all(sql, params)
            results.extend(self._row_to_relationship(r) for r in rows)

        if direction in ("child", "both"):
            stmt = sa.select(principal_relationships).where(
                principal_relationships.c.parent_id == principal_id
            )
            if relationship:
                stmt = stmt.where(principal_relationships.c.relationship == relationship)
            sql, params = compile_for(stmt, self._backend.dialect)
            rows = self._backend.fetch_all(sql, params)
            results.extend(self._row_to_relationship(r) for r in rows)

        return results

    # -- Row mapping --------------------------------------------------------

    @staticmethod
    def _row_to_principal(row: dict[str, Any]) -> Principal:
        meta_raw = row.get("metadata_json", "{}")
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
        return Principal(
            id=row["id"],
            kind=row["kind"],
            display_name=row.get("display_name", ""),
            registry_entry_id=row.get("registry_entry_id", ""),
            created_at=datetime.fromisoformat(row["created_at"]),
            created_by=row.get("created_by", "system"),
            lifecycle=Lifecycle[row.get("lifecycle", "ACTIVE")],
            metadata=Metadata(data=meta),
        )

    @staticmethod
    def _row_to_relationship(row: dict[str, Any]) -> PrincipalRelationship:
        meta_raw = row.get("metadata_json", "{}")
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
        return PrincipalRelationship(
            id=row["id"],
            parent_id=row["parent_id"],
            child_id=row["child_id"],
            relationship=row.get("relationship", "member_of"),
            created_at=datetime.fromisoformat(row["created_at"]),
            created_by=row.get("created_by", "system"),
            metadata=Metadata(data=meta),
        )
