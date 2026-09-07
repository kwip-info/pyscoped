"""From-Manifest System — declarative Scoped application creation.

Load a complete Scoped application from a single YAML/JSON manifest file.
All entities are created in dependency order, with full compliance by construction.

Usage::

    from scoped.manifest import ManifestLoader
    from scoped.storage.sa_sqlite import SASQLiteBackend

    backend = SASQLiteBackend("app.db")
    backend.initialize()

    loader = ManifestLoader(backend)
    result = loader.load("app.yaml", secret_values={"db-password": "s3cret"})

    assert result.ok
    print(f"Created {len(result.created)} entities")
"""

from scoped.manifest.exceptions import (
    ManifestError,
    ManifestLoadError,
    ManifestParseError,
    ManifestValidationError,
)
from scoped.manifest.loader import ManifestLoader, ManifestResult
from scoped.manifest.parser import parse_manifest
from scoped.manifest.resolver import ReferenceResolver
from scoped.manifest.schema import ManifestDocument
from scoped.manifest.validator import validate_manifest, validate_or_raise

__all__ = [
    "ManifestDocument",
    "ManifestError",
    "ManifestLoader",
    "ManifestLoadError",
    "ManifestParseError",
    "ManifestResult",
    "ManifestValidationError",
    "ReferenceResolver",
    "parse_manifest",
    "validate_manifest",
    "validate_or_raise",
]
