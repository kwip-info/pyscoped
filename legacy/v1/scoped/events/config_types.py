"""Typed webhook configuration models.

Provides Pydantic models for webhook endpoint config, following the
same pattern as ``scoped.rules.conditions``.

Usage::

    from scoped.events.config_types import WebhookConfig, webhook_config_to_dict

    config = WebhookConfig(headers={"Authorization": "Bearer tok"}, timeout=30)
    raw = webhook_config_to_dict(config)  # -> dict for JSON storage
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class RetryPolicy(BaseModel):
    """Webhook retry parameters."""
    model_config = ConfigDict(frozen=True)

    max_retries: int = 3
    backoff_base: int = 60


class WebhookConfig(BaseModel):
    """Typed webhook endpoint configuration.

    Fields match the keys historically stored in ``config_json``.
    """
    model_config = ConfigDict(frozen=True)

    headers: dict[str, str] = {}
    auth_token: str | None = None
    secret: str | None = None
    timeout: int = 10
    retry_policy: RetryPolicy = RetryPolicy()


def parse_webhook_config(raw: dict[str, Any]) -> WebhookConfig:
    """Parse a raw config dict into a typed ``WebhookConfig``.

    Raises ``pydantic.ValidationError`` if the structure is invalid.
    """
    return WebhookConfig.model_validate(raw)


def webhook_config_to_dict(config: WebhookConfig | dict[str, Any]) -> dict[str, Any]:
    """Serialize a webhook config to a plain dict for JSON storage.

    If already a dict, returns it unchanged (backward compatibility).
    """
    if isinstance(config, dict):
        return config
    return config.model_dump(mode="json", exclude_none=True)
