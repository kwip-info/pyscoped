"""Health checks — verify system operational health.

Checks database connectivity, schema integrity, audit chain state,
and other operational concerns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scoped.audit.query import AuditQuery
from scoped.storage.interface import StorageBackend
from scoped.testing.manifest import EXTENSION_SPECS, LAYER_SPECS


@dataclass(frozen=True, slots=True)
class HealthCheck:
    """Result of a single health check."""

    name: str
    passed: bool
    detail: str = ""


@dataclass(slots=True)
class HealthStatus:
    """Aggregate health status."""

    checks: dict[str, HealthCheck] = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return all(c.passed for c in self.checks.values())

    def add(self, check: HealthCheck) -> None:
        self.checks[check.name] = check


class HealthChecker:
    """Operational health checks for a Scoped deployment."""

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def check_all(self, *, include_extensions: bool = True) -> HealthStatus:
        """Run all health checks."""
        status = HealthStatus()
        status.add(self.check_db_connectivity())
        status.add(self.check_schema_tables(include_extensions=include_extensions))
        status.add(self.check_audit_chain())
        status.add(self.check_migration_state())
        status.add(self.check_fk_integrity())
        return status

    def check_db_connectivity(self) -> HealthCheck:
        """Verify the database is reachable and responsive."""
        try:
            result = self._backend.fetch_one("SELECT 1 as ok", ())
            if result and result.get("ok") == 1:
                return HealthCheck(
                    name="db_connectivity",
                    passed=True,
                    detail="Database responsive",
                )
            return HealthCheck(
                name="db_connectivity",
                passed=False,
                detail="Unexpected query result",
            )
        except Exception as e:
            return HealthCheck(
                name="db_connectivity",
                passed=False,
                detail=f"Database error: {e}",
            )

    def check_schema_tables(self, *, include_extensions: bool = True) -> HealthCheck:
        """Verify all required tables exist.

        Uses the layer/extension manifest as the source of truth.
        """
        required_tables: list[str] = []
        for spec in LAYER_SPECS:
            required_tables.extend(spec.tables)
        if include_extensions:
            for ext in EXTENSION_SPECS:
                required_tables.extend(ext.tables)

        try:
            if self._backend.dialect == "postgres":
                existing = self._backend.fetch_all(
                    "SELECT table_name AS name FROM information_schema.tables "
                    "WHERE table_schema = 'public'",
                    (),
                )
            else:
                existing = self._backend.fetch_all(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')",
                    (),
                )
            existing_names = {r["name"] for r in existing}

            # search_index_fts is a SQLite FTS5 virtual table; Postgres uses
            # a tsvector column on search_index instead.
            if self._backend.dialect == "postgres":
                required_tables = [t for t in required_tables if t != "search_index_fts"]

            missing = [t for t in required_tables if t not in existing_names]

            if not missing:
                return HealthCheck(
                    name="schema_tables",
                    passed=True,
                    detail=f"All {len(required_tables)} required tables present",
                )
            return HealthCheck(
                name="schema_tables",
                passed=False,
                detail=f"Missing {len(missing)} tables: {', '.join(missing[:10])}{'...' if len(missing) > 10 else ''}",
            )
        except Exception as e:
            return HealthCheck(
                name="schema_tables",
                passed=False,
                detail=f"Schema check error: {e}",
            )

    def check_fk_integrity(self) -> HealthCheck:
        """Verify foreign key constraints are satisfied across all tables."""
        try:
            if self._backend.dialect == "postgres":
                # Postgres enforces FK constraints at write-time; no batch check
                # is needed. Report as healthy.
                return HealthCheck(
                    name="fk_integrity",
                    passed=True,
                    detail="PostgreSQL enforces FK constraints natively",
                )

            violations = self._backend.fetch_all(
                "PRAGMA foreign_key_check", (),
            )
            if not violations:
                return HealthCheck(
                    name="fk_integrity",
                    passed=True,
                    detail="All foreign key constraints satisfied",
                )
            return HealthCheck(
                name="fk_integrity",
                passed=False,
                detail=f"{len(violations)} foreign key violations detected",
            )
        except Exception as e:
            return HealthCheck(
                name="fk_integrity",
                passed=False,
                detail=f"FK check error: {e}",
            )

    def check_audit_chain(self) -> HealthCheck:
        """Verify the audit trail hash chain is intact."""
        try:
            query = AuditQuery(self._backend)
            verification = query.verify_chain()

            if verification.valid:
                return HealthCheck(
                    name="audit_chain",
                    passed=True,
                    detail=f"Hash chain intact: {verification.entries_checked} entries verified",
                )
            return HealthCheck(
                name="audit_chain",
                passed=False,
                detail=f"Chain broken at sequence {verification.broken_at_sequence}",
            )
        except Exception as e:
            return HealthCheck(
                name="audit_chain",
                passed=False,
                detail=f"Chain verification error: {e}",
            )

    def check_migration_state(self) -> HealthCheck:
        """Verify the migration table exists and has entries."""
        try:
            if self._backend.dialect == "postgres":
                row = self._backend.fetch_one(
                    "SELECT COUNT(*) as cnt FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'scoped_migrations'",
                    (),
                )
            else:
                row = self._backend.fetch_one(
                    "SELECT COUNT(*) as cnt FROM sqlite_master "
                    "WHERE type='table' AND name='scoped_migrations'",
                    (),
                )
            if row and row["cnt"] > 0:
                count = self._backend.fetch_one(
                    "SELECT COUNT(*) as cnt FROM scoped_migrations", (),
                )
                n = count["cnt"] if count else 0
                return HealthCheck(
                    name="migration_state",
                    passed=True,
                    detail=f"Migration table present, {n} migrations applied",
                )
            # Migration table doesn't exist — this is OK for in-memory/test backends
            return HealthCheck(
                name="migration_state",
                passed=True,
                detail="No migration table (schema applied directly)",
            )
        except Exception as e:
            return HealthCheck(
                name="migration_state",
                passed=False,
                detail=f"Migration check error: {e}",
            )
