"""Typed plugin manifest and metadata models.

Follows the same pattern as ``scoped.rules.conditions``.

Usage::

    from scoped.integrations.plugin_types import PluginManifest

    manifest = PluginManifest(entry_point="myapp.plugin:setup", hook_points=["on_create"])
    raw = plugin_manifest_to_dict(manifest)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class PluginPermissionSpec(BaseModel):
    """A single permission requirement declared by a plugin."""
    model_config = ConfigDict(frozen=True)

    permission_type: str
    target_ref: str
    description: str = ""


class PluginManifest(BaseModel):
    """Structured plugin manifest."""
    model_config = ConfigDict(frozen=True, extra="allow")

    entry_point: str | None = None
    hook_points: list[str] = []
    required_permissions: list[PluginPermissionSpec] = []
    min_framework_version: str | None = None
    max_framework_version: str | None = None
    tags: list[str] = []


class PluginMetadata(BaseModel):
    """Structured plugin metadata."""
    model_config = ConfigDict(frozen=True, extra="allow")

    author: str | None = None
    homepage: str | None = None
    license: str | None = None
    repository: str | None = None
    keywords: list[str] = []
    category: str | None = None


def parse_plugin_manifest(raw: dict[str, Any]) -> PluginManifest:
    """Parse a raw manifest dict into a typed ``PluginManifest``."""
    return PluginManifest.model_validate(raw)


def plugin_manifest_to_dict(manifest: PluginManifest | dict[str, Any]) -> dict[str, Any]:
    """Serialize a plugin manifest to a plain dict for JSON storage."""
    if isinstance(manifest, dict):
        return manifest
    return manifest.model_dump(mode="json", exclude_none=True)


def parse_plugin_metadata(raw: dict[str, Any]) -> PluginMetadata:
    """Parse a raw metadata dict into a typed ``PluginMetadata``."""
    return PluginMetadata.model_validate(raw)


def plugin_metadata_to_dict(metadata: PluginMetadata | dict[str, Any]) -> dict[str, Any]:
    """Serialize plugin metadata to a plain dict for JSON storage."""
    if isinstance(metadata, dict):
        return metadata
    return metadata.model_dump(mode="json", exclude_none=True)
