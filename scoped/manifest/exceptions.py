"""Manifest-specific exceptions."""

from __future__ import annotations


class ManifestError(Exception):
    """Base exception for all manifest operations."""


class ManifestParseError(ManifestError):
    """Failed to parse manifest file (invalid YAML/JSON or missing required fields)."""


class ManifestValidationError(ManifestError):
    """Manifest references are invalid (dangling refs, cycles, duplicates)."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} validation error(s): {'; '.join(errors)}")


class ManifestLoadError(ManifestError):
    """Failed to load manifest into the database."""
