"""Schema migration system for Scoped storage backends.

Provides versioned, reversible schema migrations with tracking.
Migrations are Python files with up() and down() functions.
"""

from scoped.storage.migrations.base import BaseMigration
from scoped.storage.migrations.registry import MigrationRecord, MigrationRegistry
from scoped.storage.migrations.runner import MigrationRunner, MigrationStatus

__all__ = [
    "BaseMigration",
    "MigrationRecord",
    "MigrationRegistry",
    "MigrationRunner",
    "MigrationStatus",
]
