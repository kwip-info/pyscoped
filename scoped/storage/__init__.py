"""Swappable storage backends.

The storage layer provides a unified interface for all persistence operations.
Backends implement the StorageBackend protocol. SQLite is the default.
Includes a versioned migration system for schema evolution.
"""

from scoped.storage.archival import ArchiveEntry, ArchiveManager, GlacialArchive
from scoped.storage.blobs import BlobBackend, InMemoryBlobBackend, LocalBlobBackend
from scoped.storage.interface import StorageBackend, StorageTransaction
from scoped.storage.migrations import (
    BaseMigration,
    MigrationRecord,
    MigrationRegistry,
    MigrationRunner,
    MigrationStatus,
)
from scoped.storage.sqlite import SQLiteBackend
from scoped.storage.tiering import (
    RetentionPolicy,
    StorageTier,
    TierAssignment,
    TierManager,
    TierTransitionCandidate,
)

__all__ = [
    "ArchiveEntry",
    "ArchiveManager",
    "BlobBackend",
    "GCSBlobBackend",
    "GlacialArchive",
    "InMemoryBlobBackend",
    "LocalBlobBackend",
    "BaseMigration",
    "MigrationRecord",
    "MigrationRegistry",
    "MigrationRunner",
    "MigrationStatus",
    "PostgresBackend",
    "RetentionPolicy",
    "S3BlobBackend",
    "StorageBackend",
    "StorageTier",
    "StorageTransaction",
    "SQLiteBackend",
    "TierAssignment",
    "TierManager",
    "TierTransitionCandidate",
]


def __getattr__(name: str):
    if name == "PostgresBackend":
        from scoped.storage.postgres import PostgresBackend

        return PostgresBackend
    if name == "S3BlobBackend":
        from scoped.storage.blobs_s3 import S3BlobBackend

        return S3BlobBackend
    if name == "GCSBlobBackend":
        from scoped.storage.blobs_gcs import GCSBlobBackend

        return GCSBlobBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
