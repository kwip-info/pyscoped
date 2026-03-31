"""Django AppConfig for Scoped."""

from __future__ import annotations

from django.apps import AppConfig


class ScopedConfig(AppConfig):
    """Django application configuration for the Scoped framework.

    When added to ``INSTALLED_APPS``, automatically initializes the
    Scoped storage backend on ``ready()``.
    """

    name = "scoped.contrib.django"
    verbose_name = "Scoped Framework"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from scoped.contrib.django import _initialize_backend

        _initialize_backend()
