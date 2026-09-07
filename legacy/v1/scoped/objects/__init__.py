"""Layer 3: Object Versioning & Isolation.

Every data object is versioned.  Every mutation produces a new version.
Nothing is deleted — only tombstoned.  Default visibility is creator-only.
"""

from scoped.objects.blobs import BlobManager, BlobRef, BlobVersion
from scoped.objects.export import ExportPackage, Exporter
from scoped.objects.import_ import ImportResult, Importer
from scoped.objects.isolation import can_access
from scoped.objects.manager import ScopedManager
from scoped.objects.models import ObjectVersion, ScopedObject, Tombstone, compute_checksum
from scoped.objects.search import IndexEntry, SearchIndex, SearchResult
from scoped.objects.versioning import diff_versions

__all__ = [
    "BlobManager",
    "BlobRef",
    "BlobVersion",
    "ExportPackage",
    "Exporter",
    "ImportResult",
    "Importer",
    "IndexEntry",
    "ScopedManager",
    "ScopedObject",
    "ObjectVersion",
    "SearchIndex",
    "SearchResult",
    "Tombstone",
    "can_access",
    "compute_checksum",
    "diff_versions",
]
