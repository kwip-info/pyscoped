"""Isolation boundary enforcement for scoped objects."""

from __future__ import annotations


def can_access(object_owner_id: str, principal_id: str) -> bool:
    """Check if a principal can access an object based on ownership.

    Default isolation: only the owner can see it.
    Layer 4 (Tenancy) extends this with scope projections.
    """
    return object_owner_id == principal_id
