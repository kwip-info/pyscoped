from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from .exceptions import UnsupportedOperation
from .query import ScopedManager


class ScopedModel(models.Model):
    """Optional base. Adds a manager, no application fields. Register with @scoped."""

    objects = ScopedManager()

    class Meta:
        abstract = True


class Resource(models.Model):
    """Serializes history appends per application model + primary key."""

    model_label = models.CharField(max_length=255)
    object_pk = models.CharField(max_length=255)
    scope = models.CharField(max_length=255)
    revision = models.PositiveBigIntegerField(default=0)
    digest = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["model_label", "object_pk"], name="psc_resource_key")
        ]


class _AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise UnsupportedOperation("Audit events are append-only.")

    def delete(self):
        raise UnsupportedOperation("Audit events are append-only.")

    def bulk_update(self, *args, **kwargs):
        raise UnsupportedOperation("Audit events are append-only.")

    def bulk_create(self, *args, **kwargs):
        raise UnsupportedOperation(
            "Audit events must be appended through tracked model operations."
        )


class AuditEvent(models.Model):
    resource = models.ForeignKey(Resource, on_delete=models.PROTECT, related_name="events")
    revision = models.PositiveBigIntegerField()
    scope = models.CharField(max_length=255)
    actor = models.CharField(max_length=255)
    action = models.CharField(max_length=32)
    before = models.JSONField(null=True, encoder=DjangoJSONEncoder)
    after = models.JSONField(null=True, encoder=DjangoJSONEncoder)
    occurred_at = models.DateTimeField()
    reason = models.TextField(blank=True)
    request_id = models.CharField(max_length=255, blank=True)
    previous_digest = models.CharField(max_length=64, blank=True)
    digest = models.CharField(max_length=64)

    objects = _AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["revision"]
        constraints = [
            models.UniqueConstraint(fields=["resource", "revision"], name="psc_event_revision")
        ]
        indexes = [models.Index(fields=["scope", "occurred_at"], name="psc_event_scope_time")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise UnsupportedOperation("Audit events are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise UnsupportedOperation("Audit events are append-only.")
