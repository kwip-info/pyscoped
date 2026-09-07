from django.core.checks import Error, Tags, register
from django.db import models

from .query import ScopedManager, ScopedQuerySet
from .registry import registered_models


@register(Tags.models)
def check_registered_models(app_configs=None, **kwargs):
    errors = []
    for config in registered_models():
        model = config.model
        if app_configs and model._meta.app_config not in app_configs:
            continue
        for manager in model._meta.managers:
            if not isinstance(manager, ScopedManager):
                errors.append(
                    Error(
                        f"{model._meta.label}.{manager.name} must use ScopedManager; "
                        "preserve custom "
                        "querysets with ScopedManager.from_queryset().",
                        obj=model,
                        id="pyscoped.E001",
                    )
                )
            elif not issubclass(manager._queryset_class, ScopedQuerySet):
                errors.append(
                    Error(
                        f"{model._meta.label}.{manager.name}: "
                        "inherit ScopedQuerySet in custom querysets.",
                        obj=model,
                        id="pyscoped.E003",
                    )
                )
        if any(field.many_to_many for field in model._meta.get_fields()):
            errors.append(
                Error(
                    f"{model._meta.label}: implicit many-to-many mutations are not audited. "
                    "Use a registered explicit relationship model with ordinary ForeignKeys.",
                    obj=model,
                    id="pyscoped.E004",
                )
            )
        for field in model._meta.fields:
            if field.is_relation and field.remote_field.on_delete not in {
                models.CASCADE,
                models.PROTECT,
                models.RESTRICT,
                models.DO_NOTHING,
            }:
                errors.append(
                    Error(
                        f"{model._meta.label}.{field.name}: SET_NULL/SET_DEFAULT/custom on_delete "
                        "bypasses audited saves. Use PROTECT/RESTRICT or explicit "
                        "application updates.",
                        obj=field,
                        id="pyscoped.E002",
                    )
                )
    return errors
