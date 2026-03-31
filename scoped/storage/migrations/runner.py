"""Migration runner — discovers, applies, and rolls back migrations."""

from __future__ import annotations

import hashlib
import importlib
import inspect
import pkgutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

from scoped.exceptions import MigrationError
from scoped.storage.migrations.base import BaseMigration
from scoped.storage.migrations.registry import MigrationRegistry

if TYPE_CHECKING:
    from scoped.storage.interface import StorageBackend


@dataclass(frozen=True, slots=True)
class MigrationStatus:
    """Status of a single migration."""

    version: int
    name: str
    applied: bool
    applied_at: str | None = None

    def snapshot(self) -> dict:
        return {
            "version": self.version,
            "name": self.name,
            "applied": self.applied,
            "applied_at": self.applied_at,
        }


class MigrationRunner:
    """Discovers and executes schema migrations.

    Migrations are discovered from the ``versions`` subpackage by default,
    or can be registered manually.  The runner tracks applied versions in
    the ``scoped_migrations`` table and only runs pending ones.

    Usage::

        runner = MigrationRunner(backend)
        runner.apply_all()          # apply all pending
        runner.rollback_last()      # undo the most recent
        runner.get_status()         # see what's applied / pending
    """

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend
        self._registry = MigrationRegistry(backend)
        self._migrations: dict[int, BaseMigration] = {}
        self._registry.ensure_table()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, migration: BaseMigration) -> None:
        """Register a migration for management by this runner."""
        v = migration.version
        if v in self._migrations:
            raise MigrationError(
                f"Duplicate migration version {v}: "
                f"{self._migrations[v].name} and {migration.name}",
            )
        self._migrations[v] = migration

    def discover(self, package_path: str | None = None) -> int:
        """Auto-discover migrations from a Python package.

        By default discovers from ``scoped.storage.migrations.versions``.
        Returns the number of migrations discovered.
        """
        if package_path is None:
            import scoped.storage.migrations.versions as versions_pkg
            pkg = versions_pkg
        else:
            pkg = importlib.import_module(package_path)

        count = 0
        pkg_path = getattr(pkg, "__path__", None)
        if pkg_path is None:
            return 0

        for _importer, modname, _ispkg in pkgutil.iter_modules(pkg_path):
            module = importlib.import_module(f"{pkg.__name__}.{modname}")
            for _name, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, BaseMigration)
                    and obj is not BaseMigration
                    and not inspect.isabstract(obj)
                ):
                    self.register(obj())
                    count += 1
        return count

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def all_versions(self) -> list[int]:
        """All registered migration versions, sorted ascending."""
        return sorted(self._migrations.keys())

    def get_pending(self) -> list[BaseMigration]:
        """Return migrations that have not yet been applied, in order."""
        applied = set(self._registry.get_applied_versions())
        return [
            self._migrations[v]
            for v in self.all_versions
            if v not in applied
        ]

    def get_applied(self) -> list[BaseMigration]:
        """Return migrations that have been applied, in order."""
        applied = set(self._registry.get_applied_versions())
        return [
            self._migrations[v]
            for v in self.all_versions
            if v in applied
        ]

    def get_current_version(self) -> int:
        """Return the highest applied migration version, or 0."""
        return self._registry.get_current_version()

    def get_status(self) -> list[MigrationStatus]:
        """Return status of all registered migrations."""
        applied_records = {
            r.version: r for r in self._registry.get_applied_migrations()
        }
        result = []
        for v in self.all_versions:
            mig = self._migrations[v]
            record = applied_records.get(v)
            result.append(MigrationStatus(
                version=v,
                name=mig.name,
                applied=record is not None,
                applied_at=record.applied_at.isoformat() if record else None,
            ))
        return result

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def apply_all(self) -> list[int]:
        """Apply all pending migrations in order.

        Returns list of applied version numbers.
        """
        applied = []
        for mig in self.get_pending():
            self._apply_one(mig)
            applied.append(mig.version)
        return applied

    def apply_up_to(self, target_version: int) -> list[int]:
        """Apply pending migrations up to and including target_version."""
        applied = []
        for mig in self.get_pending():
            if mig.version > target_version:
                break
            self._apply_one(mig)
            applied.append(mig.version)
        return applied

    def apply_one(self, version: int) -> None:
        """Apply a specific migration by version number."""
        mig = self._migrations.get(version)
        if mig is None:
            raise MigrationError(f"Migration version {version} not found")
        if self._registry.is_applied(version):
            raise MigrationError(f"Migration {version} already applied")
        self._apply_one(mig)

    def rollback_last(self) -> int | None:
        """Roll back the most recently applied migration.

        Returns the rolled-back version, or None if nothing to roll back.
        """
        applied = self.get_applied()
        if not applied:
            return None
        last = applied[-1]
        self._rollback_one(last)
        return last.version

    def rollback_to(self, target_version: int) -> list[int]:
        """Roll back all migrations above target_version (in reverse order).

        Returns list of rolled-back version numbers.
        """
        applied = self.get_applied()
        rolled_back = []
        for mig in reversed(applied):
            if mig.version <= target_version:
                break
            self._rollback_one(mig)
            rolled_back.append(mig.version)
        return rolled_back

    def rollback_one(self, version: int) -> None:
        """Roll back a specific migration by version number."""
        mig = self._migrations.get(version)
        if mig is None:
            raise MigrationError(f"Migration version {version} not found")
        if not self._registry.is_applied(version):
            raise MigrationError(f"Migration {version} is not applied")
        self._rollback_one(mig)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply_one(self, mig: BaseMigration) -> None:
        """Apply a single migration and record it."""
        try:
            mig.up(self._backend)
        except Exception as exc:
            raise MigrationError(
                f"Failed to apply migration {mig.version} ({mig.name}): {exc}",
                context={"version": mig.version, "name": mig.name},
            ) from exc
        checksum = self._compute_checksum(mig)
        self._registry.record_applied(mig.version, mig.name, checksum)

    def _rollback_one(self, mig: BaseMigration) -> None:
        """Roll back a single migration and remove its record."""
        try:
            mig.down(self._backend)
        except Exception as exc:
            raise MigrationError(
                f"Failed to roll back migration {mig.version} ({mig.name}): {exc}",
                context={"version": mig.version, "name": mig.name},
            ) from exc
        self._registry.record_rolled_back(mig.version)

    @staticmethod
    def _compute_checksum(mig: BaseMigration) -> str:
        """Compute a checksum for a migration (based on class source if available)."""
        try:
            source = inspect.getsource(mig.__class__)
            return hashlib.sha256(source.encode()).hexdigest()[:16]
        except (OSError, TypeError):
            return ""
