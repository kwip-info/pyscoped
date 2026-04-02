"""Migration 0014: Add UNIQUE constraint on audit_trail.sequence.

Prevents multi-process sequence collisions at the database level.
The AuditWriter uses an in-memory counter with re-seeding, but without
a database constraint, concurrent processes can assign duplicate sequence
numbers during the re-seed race window.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scoped.storage.migrations.base import BaseMigration

if TYPE_CHECKING:
    from scoped.storage.interface import StorageBackend


class AddAuditSequenceUnique(BaseMigration):
    @property
    def version(self) -> int:
        return 14

    @property
    def name(self) -> str:
        return "audit_sequence_unique"

    def up(self, backend: StorageBackend) -> None:
        # First, detect and fix any pre-existing duplicate sequences.
        # Renumber duplicates by adding a large offset to make them unique.
        dupes = backend.fetch_all(
            "SELECT sequence, COUNT(*) AS cnt FROM audit_trail "
            "GROUP BY sequence HAVING COUNT(*) > 1",
            (),
        )
        if dupes:
            # Get the current max sequence to use as a safe renumbering base
            row = backend.fetch_one(
                "SELECT COALESCE(MAX(sequence), 0) AS max_seq FROM audit_trail",
                (),
            )
            offset = (row["max_seq"] if row else 0) + 1
            for dupe in dupes:
                seq = dupe["sequence"]
                # Get all IDs with this sequence except the first
                rows = backend.fetch_all(
                    "SELECT id FROM audit_trail WHERE sequence = ? ORDER BY id",
                    (seq,),
                )
                for i, r in enumerate(rows[1:], start=1):
                    backend.execute(
                        "UPDATE audit_trail SET sequence = ? WHERE id = ?",
                        (offset, r["id"]),
                    )
                    offset += 1

        if backend.dialect == "postgres":
            backend.execute(
                "ALTER TABLE audit_trail "
                "ADD CONSTRAINT uq_audit_sequence UNIQUE (sequence)"
            )
        else:
            # SQLite
            backend.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_audit_sequence ON audit_trail(sequence)"
            )

    def down(self, backend: StorageBackend) -> None:
        if backend.dialect == "postgres":
            backend.execute(
                "ALTER TABLE audit_trail "
                "DROP CONSTRAINT IF EXISTS uq_audit_sequence"
            )
        else:
            backend.execute("DROP INDEX IF EXISTS uq_audit_sequence")
