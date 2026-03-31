"""Sync agent configuration.

Controls how the background sync agent connects to the management
plane, how often it pushes, and how it handles errors.

Usage::

    from scoped.sync.config import SyncConfig

    config = SyncConfig(
        interval_seconds=30,
        batch_size=1000,
    )
    client = scoped.init(
        database_url="postgresql://...",
        api_key="psc_live_...",
        sync_config=config,
    )
    client.start_sync()
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SyncConfig:
    """Configuration for the sync agent.

    Attributes:
        base_url: Management plane API base URL.
        interval_seconds: Seconds between sync cycles.
        batch_size: Maximum audit entries per sync batch.
        max_retries: Max consecutive failures before stopping.
        retry_base_delay_seconds: Base delay for exponential backoff.
        retry_max_delay_seconds: Maximum backoff delay cap.
        request_timeout_seconds: HTTP request timeout.
        auto_start: If True, sync starts automatically on client init.
    """

    base_url: str = "https://api.pyscoped.dev/v1"
    interval_seconds: int = 60
    batch_size: int = 500
    max_retries: int = 5
    retry_base_delay_seconds: float = 1.0
    retry_max_delay_seconds: float = 300.0
    request_timeout_seconds: int = 30
    auto_start: bool = False
