"""Version creation and diffing for scoped objects."""

from __future__ import annotations

from typing import Any

from scoped.objects.models import ObjectVersion


def diff_versions(old: ObjectVersion, new: ObjectVersion) -> dict[str, Any]:
    """Compute a field-level diff between two object versions.

    Returns a dict with 'added', 'removed', and 'changed' keys.
    Each 'changed' entry is {'old': ..., 'new': ...}.
    """
    old_data = old.data
    new_data = new.data

    added: dict[str, Any] = {}
    removed: dict[str, Any] = {}
    changed: dict[str, Any] = {}

    all_keys = set(old_data) | set(new_data)
    for key in all_keys:
        if key not in old_data:
            added[key] = new_data[key]
        elif key not in new_data:
            removed[key] = old_data[key]
        elif old_data[key] != new_data[key]:
            changed[key] = {"old": old_data[key], "new": new_data[key]}

    return {"added": added, "removed": removed, "changed": changed}
