"""Explicit model registration, with no database or user-schema changes."""

from copy import copy
from dataclasses import dataclass
from functools import wraps
from inspect import signature

from django.core.exceptions import ImproperlyConfigured
from django.db import router, transaction
from django.db.models.signals import pre_delete

from .context import current_context
from .exceptions import UnsupportedOperation


@dataclass(frozen=True)
class Registration:
    model: type
    scope_field: str
    fields: tuple
    mode: str

    @property
    def scope_attname(self):
        return self.model._meta.get_field(self.scope_field).attname


_registry = {}


class _ScopedForwardRelation:
    def __get__(self, instance, cls=None):
        if instance is None:
            return super().__get__(instance, cls)
        from .audit import check_scope

        ctx = current_context()
        related = super().__get__(instance, cls)
        if related is not None:
            check_scope(self._pyscoped_target, related, ctx)
        return related

    def get_queryset(self, **hints):
        # Django normally uses the unfiltered internal base manager here.
        # Scoped forward relations and default prefetch must use the target's
        # public scope predicate, including the to_attr prefetch path.
        return self._pyscoped_target.model._default_manager.db_manager(hints=hints).all()


def install_relation_guards():
    from django.apps import apps

    protected = {model: config for model, config in _registry.items() if config.mode == "enforce"}
    for model in protected:
        for field in model._meta.fields:
            if not field.is_relation:
                continue
            target = protected.get(field.remote_field.model)
            if target is None:
                continue
            descriptor = getattr(model, field.name)
            if isinstance(descriptor, _ScopedForwardRelation):
                continue
            wrapped = copy(descriptor)
            wrapped.__class__ = type(
                "ScopedRelation", (_ScopedForwardRelation, type(descriptor)), {}
            )
            wrapped._pyscoped_target = target
            setattr(model, field.name, wrapped)
        if apps.models_ready:
            for relation in model._meta.related_objects:
                target = protected.get(relation.related_model)
                if not relation.one_to_one or target is None or relation.hidden:
                    continue
                name = relation.get_accessor_name()
                descriptor = getattr(model, name)
                if isinstance(descriptor, _ScopedForwardRelation):
                    continue
                wrapped = copy(descriptor)
                wrapped.__class__ = type(
                    "ScopedReverseRelation", (_ScopedForwardRelation, type(descriptor)), {}
                )
                wrapped._pyscoped_target = target
                setattr(model, name, wrapped)


def registration(model):
    try:
        return _registry[model]
    except KeyError:
        raise ImproperlyConfigured(f"{model._meta.label} must be registered with @pyscoped.scoped.")


def registered_models():
    return tuple(_registry.values())


def register(model, *, scope_field, fields, mode="enforce"):
    from .query import ScopedManager, ScopedQuerySet

    if model._meta.abstract or model._meta.proxy or model._meta.parents:
        raise ImproperlyConfigured("Register a concrete model without multi-table inheritance.")
    if model._meta.is_composite_pk:
        raise ImproperlyConfigured("Composite primary keys are outside initial 2.0 support.")
    for manager in model._meta.managers:
        if not isinstance(manager, ScopedManager) or not issubclass(
            manager._queryset_class, ScopedQuerySet
        ):
            raise ImproperlyConfigured(
                f"{model._meta.label}.{manager.name} needs ScopedManager and ScopedQuerySet."
            )
    if mode not in {"audit", "enforce"}:
        raise ImproperlyConfigured("mode must be 'audit' or 'enforce'.")
    if not isinstance(fields, (tuple, list)) or not fields:
        raise ImproperlyConfigured("Supply a nonempty explicit audit field allowlist.")
    names = tuple(fields)
    for name in (scope_field, *names):
        try:
            field = model._meta.get_field(name)
        except Exception as exc:
            raise ImproperlyConfigured(f"Unknown field {name!r} on {model._meta.label}.") from exc
        if not field.concrete or field.many_to_many or field.primary_key:
            raise ImproperlyConfigured("Scope/audit fields must be concrete, non-PK columns.")
    if model._meta.get_field(scope_field).null:
        raise ImproperlyConfigured("The scope field must be non-nullable.")
    config = Registration(model, scope_field, names, mode)
    if model in _registry:
        if _registry[model] != config:
            raise ImproperlyConfigured("A model cannot be registered twice with different options.")
        return model
    _registry[model] = config
    original = model.save_base
    original_signature = signature(original)

    @wraps(original)
    def save_base(instance, *args, **kwargs):
        from .audit import _action, append_event, check_relations, check_scope, load_row, snapshot

        ctx = current_context()
        bound = original_signature.bind(instance, *args, **kwargs)
        if bound.arguments.get("raw", False):
            raise UnsupportedOperation("Raw fixture saves bypass guarantees; use a data migration.")
        using = bound.arguments.get("using") or router.db_for_write(model, instance=instance)
        with transaction.atomic(using=using):
            before_row = load_row(model, instance.pk, using, lock=True)
            if before_row is not None:
                check_scope(config, before_row, ctx)
            check_scope(config, instance, ctx)
            check_relations(config, instance, ctx, using)
            if before_row is not None:
                if getattr(before_row, config.scope_attname) != getattr(
                    instance, config.scope_attname
                ):
                    raise UnsupportedOperation(
                        "Scope changes require an explicit application migration."
                    )
            before = snapshot(config, before_row)
            result = original(instance, *args, **kwargs)
            after_row = load_row(model, instance.pk, using)
            if after_row is None:
                raise UnsupportedOperation("The save did not leave a persisted row.")
            check_scope(config, after_row, ctx)
            check_relations(config, after_row, ctx, using)
            override = _action.get()
            action = "create" if before_row is None else "update"
            if override is not None and override[:2] == (model, instance.pk):
                action = override[2]
            append_event(
                config,
                after_row,
                using=using,
                action=action,
                before=before,
                after=snapshot(config, after_row),
                ctx=ctx,
            )
            return result

    model.save_base = save_base

    # Savepoints keep a rejected nested operation from poisoning the caller's
    # transaction. Wrapping save also includes custom model business logic.
    for method_name in ("save", "delete"):
        original_method = getattr(model, method_name)
        method_signature = signature(original_method)

        def wrap_method(method, method_sig):
            @wraps(method)
            def wrapped(instance, *args, **kwargs):
                current_context()
                bound = method_sig.bind(instance, *args, **kwargs)
                using = bound.arguments.get("using") or router.db_for_write(
                    model, instance=instance
                )
                with transaction.atomic(using=using):
                    return method(instance, *args, **kwargs)

            return wrapped

        setattr(model, method_name, wrap_method(original_method, method_signature))

    original_refresh = model.refresh_from_db

    @wraps(original_refresh)
    def refresh(instance, using=None, fields=None, **kwargs):
        from .audit import check_scope, load_row

        if config.mode == "enforce":
            ctx = current_context()
            alias = using or instance._state.db or router.db_for_read(model, instance=instance)
            row = load_row(model, instance.pk, alias)
            if row is not None:
                check_scope(config, row, ctx)
        return original_refresh(instance, using=using, fields=fields, **kwargs)

    model.refresh_from_db = refresh

    def before_delete(sender, instance, using, **kwargs):
        from .audit import append_event, check_scope, load_row, snapshot

        ctx = current_context()
        row = load_row(model, instance.pk, using, lock=True)
        if row is not None:
            check_scope(config, row, ctx)
            append_event(
                config,
                row,
                using=using,
                action="delete",
                before=snapshot(config, row),
                after=None,
                ctx=ctx,
            )

    pre_delete.connect(
        before_delete,
        sender=model,
        weak=False,
        dispatch_uid=f"pyscoped.delete.{model._meta.label_lower}",
    )
    install_relation_guards()
    return model


def scoped(*, scope_field, fields, mode="enforce"):
    """Attach PyScoped to an existing model; use ScopedManager on registered models."""

    def decorate(model):
        return register(model, scope_field=scope_field, fields=fields, mode=mode)

    return decorate
