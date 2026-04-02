"""Typed scope setting values.

Scope settings are generic key-value pairs, so unlike webhooks or gates
there is no single schema.  Instead, this module provides:

1. A registry mapping setting keys to Pydantic models
2. A ``setting_value_to_dict`` helper that serializes Pydantic models
3. Callers can register types per key for opt-in validation

Usage::

    from scoped.tenancy.config_types import register_setting_type, setting_value_to_dict
    from pydantic import BaseModel

    class ThemeConfig(BaseModel):
        mode: str = "light"
        primary_color: str = "#0066cc"

    register_setting_type("theme", ThemeConfig)

    # Now ConfigStore.set() can accept ThemeConfig instances
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


# Key → Pydantic model class
_SETTING_TYPES: dict[str, type[BaseModel]] = {}


def register_setting_type(key: str, model_cls: type[BaseModel]) -> None:
    """Register a Pydantic model for a setting key.

    Once registered, ``parse_setting_value(key, raw)`` will validate
    against this model.
    """
    _SETTING_TYPES[key] = model_cls


def get_setting_type(key: str) -> type[BaseModel] | None:
    """Return the registered model for a key, or None."""
    return _SETTING_TYPES.get(key)


def parse_setting_value(key: str, raw: Any) -> BaseModel | Any:
    """Parse a raw value into its typed model if a type is registered.

    Returns the raw value unchanged if no type is registered for the key
    or if the value is not a dict (scalars pass through).
    """
    model_cls = _SETTING_TYPES.get(key)
    if model_cls is None:
        return raw
    if isinstance(raw, dict):
        return model_cls.model_validate(raw)
    return raw


def setting_value_to_dict(value: BaseModel | Any) -> Any:
    """Serialize a Pydantic model to a plain value for JSON storage.

    If already a non-model value, returns it unchanged (backward compat).
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    return value
