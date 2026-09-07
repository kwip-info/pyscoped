import hashlib
import json
from contextvars import ContextVar

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from django.utils import timezone

from .context import identity
from .exceptions import ScopeViolation

_action = ContextVar("pyscoped_audit_action", default=None)


def plain(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True, allow_nan=False))


def load_row(model, pk, using, *, lock=False):
    if pk is None:
        return None
    qs = models.QuerySet(model=model, using=using).filter(pk=pk)
    if lock:
        qs = qs.select_for_update()
    return qs.first()


def snapshot(config, instance):
    if instance is None:
        return None
    return plain(
        {
            name: getattr(instance, config.model._meta.get_field(name).attname)
            for name in config.fields
        }
    )


def check_scope(config, instance, ctx):
    actual = identity(getattr(instance, config.scope_attname))
    expected = (
        config.model._meta.get_field(config.scope_field).target_field
        if config.model._meta.get_field(config.scope_field).is_relation
        else config.model._meta.get_field(config.scope_field)
    )
    if config.mode == "enforce" and expected.to_python(actual) != expected.to_python(ctx.scope):
        raise ScopeViolation(f"{config.model._meta.label} belongs to a different scope.")
    return actual


def check_relations(config, instance, ctx, using):
    from .registry import registered_models

    if config.mode != "enforce":
        return
    protected = {item.model: item for item in registered_models() if item.mode == "enforce"}
    for field in config.model._meta.fields:
        if not field.is_relation or field.related_model not in protected:
            continue
        value = getattr(instance, field.attname)
        if value is None:
            continue
        # Respect to_field instead of assuming every FK references the PK.
        related = (
            models.QuerySet(model=field.related_model, using=using)
            .filter(**{field.target_field.attname: value})
            .select_for_update()
            .first()
        )
        if related is not None:
            check_scope(protected[field.related_model], related, ctx)


def event_digest(event):
    payload = {
        name: getattr(event, name)
        for name in (
            "revision",
            "scope",
            "actor",
            "action",
            "before",
            "after",
            "occurred_at",
            "reason",
            "request_id",
            "previous_digest",
        )
    }
    payload["model_label"] = event.resource.model_label
    payload["object_pk"] = event.resource.object_pk
    encoded = json.dumps(
        payload, cls=DjangoJSONEncoder, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def append_event(config, row, *, using, action, before, after, ctx):
    from .models import AuditEvent, Resource

    with transaction.atomic(using=using):
        resource, _ = Resource.objects.using(using).get_or_create(
            model_label=config.model._meta.label_lower,
            object_pk=identity(row.pk),
            defaults={"scope": identity(getattr(row, config.scope_attname))},
        )
        resource = Resource.objects.using(using).select_for_update().get(pk=resource.pk)
        if resource.scope != identity(getattr(row, config.scope_attname)):
            raise ScopeViolation(
                "An existing history identity cannot be reassigned to another scope."
            )
        event = AuditEvent(
            resource=resource,
            revision=resource.revision + 1,
            scope=identity(getattr(row, config.scope_attname)),
            actor=ctx.actor,
            action=action,
            before=before,
            after=after,
            occurred_at=timezone.now(),
            reason=ctx.reason,
            request_id=ctx.request_id,
            previous_digest=resource.digest,
        )
        event.digest = event_digest(event)
        event.save(using=using, force_insert=True)
        resource.revision = event.revision
        resource.digest = event.digest
        resource.save(using=using, update_fields=["revision", "digest"])
        return event
