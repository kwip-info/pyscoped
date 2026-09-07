from django.apps import AppConfig


class PyScopedConfig(AppConfig):
    name = "pyscoped"
    verbose_name = "PyScoped"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Registration and checks only. No database access at process startup.
        from . import checks  # noqa: F401
        from .registry import install_relation_guards

        install_relation_guards()
