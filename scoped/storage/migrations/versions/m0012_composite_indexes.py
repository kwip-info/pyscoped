"""Migration 0012: Add composite indexes for performance.

Adds covering indexes on frequently-joined columns that currently
only have single-column indexes. Targets:

- scope_projections (scope_id, lifecycle) — visibility JOIN
- scope_memberships (scope_id, lifecycle) — visibility JOIN
- scope_memberships (principal_id, lifecycle) — principal scope lookup
- audit_trail (action, timestamp) — rate-limit counting
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scoped.storage.migrations.base import BaseMigration

if TYPE_CHECKING:
    from scoped.storage.interface import StorageBackend


_UP_SQL = """\
CREATE INDEX IF NOT EXISTS idx_projections_scope_lifecycle
    ON scope_projections (scope_id, lifecycle);

CREATE INDEX IF NOT EXISTS idx_memberships_scope_lifecycle
    ON scope_memberships (scope_id, lifecycle);

CREATE INDEX IF NOT EXISTS idx_memberships_principal_lifecycle
    ON scope_memberships (principal_id, lifecycle);

CREATE INDEX IF NOT EXISTS idx_audit_action_timestamp
    ON audit_trail (action, timestamp);
"""

_DOWN_SQL = """\
DROP INDEX IF EXISTS idx_projections_scope_lifecycle;
DROP INDEX IF EXISTS idx_memberships_scope_lifecycle;
DROP INDEX IF EXISTS idx_memberships_principal_lifecycle;
DROP INDEX IF EXISTS idx_audit_action_timestamp;
"""


class AddCompositeIndexes(BaseMigration):
    @property
    def version(self) -> int:
        return 12

    @property
    def name(self) -> str:
        return "composite_indexes"

    def up(self, backend: StorageBackend) -> None:
        backend.execute_script(_UP_SQL)

    def down(self, backend: StorageBackend) -> None:
        backend.execute_script(_DOWN_SQL)
