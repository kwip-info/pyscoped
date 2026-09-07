"""Migration 0015: Add indexes for environment hot paths.

Adds covering indexes on environment tables for frequently queried
columns:

- environments (owner_id, created_at) — list_environments by owner
- environments (state) — list_environments by state
- environment_objects (environment_id, origin) — get_created/projected_object_ids
- environment_snapshots (environment_id, created_at) — list_snapshots
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scoped.storage.migrations.base import BaseMigration

if TYPE_CHECKING:
    from scoped.storage.interface import StorageBackend


_UP_SQL = """\
CREATE INDEX IF NOT EXISTS idx_environments_owner_created
    ON environments (owner_id, created_at);

CREATE INDEX IF NOT EXISTS idx_environments_state
    ON environments (state);

CREATE INDEX IF NOT EXISTS idx_env_objects_env_origin
    ON environment_objects (environment_id, origin);

CREATE INDEX IF NOT EXISTS idx_env_snapshots_env_created
    ON environment_snapshots (environment_id, created_at);
"""

_DOWN_SQL = """\
DROP INDEX IF EXISTS idx_environments_owner_created;
DROP INDEX IF EXISTS idx_environments_state;
DROP INDEX IF EXISTS idx_env_objects_env_origin;
DROP INDEX IF EXISTS idx_env_snapshots_env_created;
"""


class AddEnvironmentIndexes(BaseMigration):
    @property
    def version(self) -> int:
        return 15

    @property
    def name(self) -> str:
        return "environment_indexes"

    def up(self, backend: StorageBackend) -> None:
        backend.execute_script(_UP_SQL)

    def down(self, backend: StorageBackend) -> None:
        backend.execute_script(_DOWN_SQL)
