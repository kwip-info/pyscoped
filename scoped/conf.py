"""Framework configuration.

Central configuration that all layers read from. Can be overridden
by Django settings when running as a plugin, or set directly for standalone use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScopedConfig:
    """Global configuration for the Scoped framework."""

    # Storage backend identifier (e.g., "sqlite", "django_orm", "postgres")
    storage_backend: str = "sqlite"

    # SQLite database path (when using sqlite backend)
    sqlite_path: str = ":memory:"

    # Whether to enforce compliance checks at runtime (not just test time)
    runtime_compliance: bool = True

    # Whether read operations produce audit traces
    trace_reads: bool = True

    # Hash algorithm for audit chain integrity
    audit_hash_algorithm: str = "sha256"

    # Maximum number of trace entries to batch before flushing
    audit_batch_size: int = 100

    # Whether rollbacks require explicit confirmation
    rollback_requires_confirmation: bool = False

    # Additional backend-specific settings
    backend_options: dict[str, Any] = field(default_factory=dict)


# Singleton config — layers import and read this.
# Override by assigning to `scoped.conf.config` before framework init.
config = ScopedConfig()


def configure(**kwargs: Any) -> ScopedConfig:
    """Update the global config. Returns the updated config."""
    global config
    for key, value in kwargs.items():
        if not hasattr(config, key):
            raise ValueError(f"Unknown config key: {key}")
        setattr(config, key, value)
    return config
