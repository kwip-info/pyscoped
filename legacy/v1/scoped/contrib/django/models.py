"""Django model mixin that syncs with pyscoped's object layer.

When a Django model inherits from ``ScopedModel``, every ``save()`` and
``delete()`` automatically creates/updates/tombstones the corresponding
pyscoped ``ScopedObject``.  Versioning, audit trails, and isolation
enforcement happen automatically.

Usage::

    from scoped.contrib.django.models import ScopedModel

    class Invoice(ScopedModel):
        amount = models.DecimalField(max_digits=10, decimal_places=2)
        currency = models.CharField(max_length=3)

        class ScopedMeta:
            object_type = "invoice"
            scoped_fields = ["amount", "currency"]  # None = all fields
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterator

from django.db import models, transaction

logger = logging.getLogger(__name__)

# Fields that are always excluded from scoped serialization
_EXCLUDED_FIELDS = frozenset({"pk", "id", "scoped_object_id", "scoped_owner_id"})


class ScopedQuerySet(models.QuerySet):
    """QuerySet with pyscoped visibility filtering."""

    def for_principal(self, principal_id: str) -> ScopedQuerySet:
        """Filter to objects owned by or visible to this principal.

        Uses pyscoped's visibility engine when available, falling back
        to simple owner filtering.
        """
        from scoped.contrib.django import get_client

        client = get_client()
        if client is None:
            return self.filter(scoped_owner_id=principal_id)

        try:
            # Get owned + projected visible object IDs from pyscoped
            visible_ids = [
                obj.id
                for obj in client.services.manager.list_objects(
                    principal_id=principal_id,
                )
            ]

            # Also try scope visibility engine if available
            try:
                from scoped.tenancy.engine import VisibilityEngine

                engine = VisibilityEngine(client.services.backend)
                scope_visible = engine.visible_object_ids(principal_id)
                visible_ids.extend(scope_visible)
            except Exception:
                pass

            # Deduplicate while preserving order
            seen: set[str] = set()
            unique_ids: list[str] = []
            for oid in visible_ids:
                if oid not in seen:
                    seen.add(oid)
                    unique_ids.append(oid)

            return self.filter(scoped_object_id__in=unique_ids)
        except Exception:
            # Fallback to owner-based filtering
            return self.filter(scoped_owner_id=principal_id)


class ScopedDjangoManager(models.Manager):
    """Manager that returns ScopedQuerySet with visibility filtering."""

    def get_queryset(self) -> ScopedQuerySet:
        return ScopedQuerySet(self.model, using=self._db)

    def for_principal(self, principal_id: str) -> ScopedQuerySet:
        """Shortcut for ``get_queryset().for_principal(...)``."""
        return self.get_queryset().for_principal(principal_id)


class ScopedModel(models.Model):
    """Abstract base model that syncs with pyscoped's object layer.

    When a Django model inherits from ``ScopedModel``, every save/delete
    automatically creates/updates/tombstones the corresponding pyscoped
    ``ScopedObject``.  Versioning, audit trails, and isolation enforcement
    happen automatically.

    Usage::

        class Invoice(ScopedModel):
            amount = models.DecimalField(max_digits=10, decimal_places=2)
            currency = models.CharField(max_length=3)

            class ScopedMeta:
                object_type = "invoice"
                scoped_fields = ["amount", "currency"]  # None = all fields
    """

    scoped_object_id = models.CharField(max_length=64, blank=True, db_index=True)
    scoped_owner_id = models.CharField(max_length=64, blank=True, db_index=True)

    # Keep Django's default manager untouched
    objects = models.Manager()

    # Secondary manager with pyscoped visibility filtering
    scoped_objects = ScopedDjangoManager()

    class Meta:
        abstract = True

    class ScopedMeta:
        object_type: str = ""  # Subclasses MUST override
        scoped_fields: list[str] | None = None  # Which fields to sync; None = all

    # ------------------------------------------------------------------
    # save() override
    # ------------------------------------------------------------------

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist to Django DB, then sync to pyscoped.

        On first save (no ``scoped_object_id``): creates a new
        ``ScopedObject`` via the manager.

        On subsequent saves: creates a new version via update.

        If no ``ScopedContext`` is active, falls back to
        ``self.scoped_owner_id``.  If that is also empty, the Django
        save proceeds but no pyscoped sync happens.
        """
        from scoped.contrib.django import get_client
        from scoped.identity.context import ScopedContext

        client = get_client()
        principal_id = self._resolve_principal_id()

        with transaction.atomic():
            # Persist to Django first
            is_new = not self.scoped_object_id
            super().save(*args, **kwargs)

            # Skip scoped sync if no client or no principal
            if client is None or not principal_id:
                return

            data = self._to_scoped_dict()
            object_type = self._get_object_type()

            if not object_type:
                logger.warning(
                    "ScopedModel.save(): ScopedMeta.object_type is empty on %s; "
                    "skipping pyscoped sync.",
                    type(self).__name__,
                )
                return

            try:
                if is_new:
                    obj, _ver = client.services.manager.create(
                        object_type=object_type,
                        owner_id=principal_id,
                        data=data,
                    )
                    # Store the pyscoped IDs back on the Django model
                    self.scoped_object_id = obj.id
                    self.scoped_owner_id = obj.owner_id
                    # Save the IDs without re-triggering the full save
                    type(self).objects.filter(pk=self.pk).update(
                        scoped_object_id=self.scoped_object_id,
                        scoped_owner_id=self.scoped_owner_id,
                    )
                else:
                    client.services.manager.update(
                        self.scoped_object_id,
                        principal_id=principal_id,
                        data=data,
                    )
            except Exception:
                logger.exception(
                    "ScopedModel.save(): pyscoped sync failed for %s (pk=%s)",
                    type(self).__name__,
                    self.pk,
                )
                raise

    # ------------------------------------------------------------------
    # delete() override
    # ------------------------------------------------------------------

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Tombstone in pyscoped, then delete from Django DB.

        If no ``scoped_object_id`` is set or no client is available,
        the Django delete proceeds without pyscoped sync.
        """
        from scoped.contrib.django import get_client

        principal_id = self._resolve_principal_id()

        with transaction.atomic():
            # Tombstone in pyscoped first (before Django deletes the row)
            if self.scoped_object_id:
                client = get_client()
                if client is not None and principal_id:
                    try:
                        client.services.manager.tombstone(
                            self.scoped_object_id,
                            principal_id=principal_id,
                            reason=f"Deleted via Django ORM ({type(self).__name__})",
                        )
                    except Exception:
                        logger.exception(
                            "ScopedModel.delete(): pyscoped tombstone failed for %s "
                            "(pk=%s, scoped_object_id=%s)",
                            type(self).__name__,
                            self.pk,
                            self.scoped_object_id,
                        )
                        raise

            return super().delete(*args, **kwargs)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def _to_scoped_dict(self) -> dict[str, Any]:
        """Serialize Django model fields to a JSON-compatible dict.

        Respects ``ScopedMeta.scoped_fields`` (``None`` means all
        non-internal fields).  Handles common Django field types:

        - ``DateTimeField`` / ``DateField`` -> ISO 8601 string
        - ``ForeignKey`` -> ``str(pk)``
        - ``DecimalField`` -> ``str``
        - ``UUIDField`` -> ``str``
        - All others -> value as-is
        """
        allowed_fields = self._get_scoped_fields()
        result: dict[str, Any] = {}

        for field in self._meta.get_fields():
            # Skip relations that aren't concrete columns
            if not hasattr(field, "attname"):
                continue

            name = field.name

            # Skip internal fields
            if name in _EXCLUDED_FIELDS:
                continue

            # Skip ForeignKey descriptor names (use attname for the raw FK column)
            if isinstance(field, models.ForeignKey):
                col_name = field.attname  # e.g. "author_id"
                if allowed_fields is not None and name not in allowed_fields:
                    continue
                value = getattr(self, col_name, None)
                result[name] = str(value) if value is not None else None
                continue

            # Filter by scoped_fields
            if allowed_fields is not None and name not in allowed_fields:
                continue

            value = getattr(self, name, None)
            result[name] = self._serialize_value(field, value)

        return result

    @staticmethod
    def _serialize_value(field: models.Field, value: Any) -> Any:
        """Convert a single field value to a JSON-compatible type."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, uuid.UUID):
            return str(value)
        return value

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_principal_id(self) -> str:
        """Determine the acting principal ID.

        Priority:
        1. Active ``ScopedContext`` (set by middleware or context manager)
        2. ``self.scoped_owner_id`` (fallback for background tasks)
        3. Empty string (no sync will happen)
        """
        from scoped.identity.context import ScopedContext

        ctx = ScopedContext.current_or_none()
        if ctx is not None:
            return ctx.principal_id
        return self.scoped_owner_id or ""

    def _get_object_type(self) -> str:
        """Read ``object_type`` from the model's ``ScopedMeta``."""
        meta = getattr(type(self), "ScopedMeta", None)
        if meta is None:
            return ""
        return getattr(meta, "object_type", "")

    def _get_scoped_fields(self) -> list[str] | None:
        """Read ``scoped_fields`` from the model's ``ScopedMeta``."""
        meta = getattr(type(self), "ScopedMeta", None)
        if meta is None:
            return None
        return getattr(meta, "scoped_fields", None)


# ------------------------------------------------------------------
# Context helper for non-HTTP code
# ------------------------------------------------------------------


@contextmanager
def scoped_context_for(principal_id: str) -> Iterator[None]:
    """Set ``ScopedContext`` for non-HTTP code (management commands, Celery tasks).

    Usage::

        with scoped_context_for("user-123"):
            Invoice.objects.create(amount=100, currency="USD")
    """
    from scoped.contrib.django import get_client
    from scoped.identity.context import ScopedContext

    client = get_client()
    if client is None:
        yield
        return

    principal = client.services.principals.get_principal(principal_id)
    with ScopedContext(principal):
        yield
