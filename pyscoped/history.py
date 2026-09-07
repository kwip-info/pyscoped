"""Scoped history, truthful baselines, and explicit limited restoration."""

from dataclasses import dataclass

from django.db import router, transaction

from .audit import _action, append_event, check_scope, event_digest, load_row, snapshot
from .context import current_context, identity
from .exceptions import HistoryConflict, ScopeViolation, UnsupportedOperation
from .registry import registration


def _resource(model, pk, using):
    from .models import Resource

    config = registration(model)
    ctx = current_context()
    resource = (
        Resource.objects.using(using)
        .filter(model_label=model._meta.label_lower, object_pk=identity(pk))
        .first()
    )
    if resource is not None:
        field = model._meta.get_field(config.scope_field)
        field = field.target_field if field.is_relation else field
        if field.to_python(resource.scope) != field.to_python(ctx.scope):
            raise ScopeViolation("History belongs to another scope.")
    return resource


def history(model, pk, *, using=None):
    """Return an eagerly authorized list. Audit tables themselves are internal/admin APIs."""
    using = using or router.db_for_read(model)
    resource = _resource(model, pk, using)
    if resource is None:
        return []
    return list(resource.events.using(using).select_related("resource").order_by("revision"))


@dataclass(frozen=True)
class Verification:
    valid: bool
    events: int
    detail: str = ""


def verify_history(model, pk, *, using=None):
    """Verify against the retained head. Does not defeat an administrator rewriting both."""
    using = using or router.db_for_read(model)
    # Locking the head makes verification consistent with concurrent appends.
    with transaction.atomic(using=using):
        resource = _resource(model, pk, using)
        if resource is None:
            return Verification(False, 0, "No history exists.")
        from .models import Resource

        resource = Resource.objects.using(using).select_for_update().get(pk=resource.pk)
        events = list(resource.events.using(using).select_related("resource").order_by("revision"))
        previous = ""
        for revision, event in enumerate(events, start=1):
            if (
                event.revision != revision
                or event.previous_digest != previous
                or event.scope != resource.scope
                or event.digest != event_digest(event)
            ):
                return Verification(False, len(events), f"Invalid event at revision {revision}.")
            previous = event.digest
        if resource.revision != len(events) or resource.digest != previous:
            return Verification(False, len(events), "Head does not match retained events.")
        return Verification(True, len(events))


@dataclass(frozen=True)
class BackfillResult:
    examined: int
    pending: int
    created: int
    skipped: int
    dry_run: bool


def backfill(model, *, using=None, dry_run=True, batch_size=500):
    """Baseline the current scope; resumable per-row transactions, never invented past events."""
    from .models import Resource

    ctx = current_context()
    config = registration(model)
    using = using or router.db_for_write(model)
    if batch_size < 1:
        raise ValueError("batch_size must be positive.")
    pks = (
        model._default_manager.using(using)
        .filter(**{config.scope_attname: ctx.scope})
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    examined = pending = created = skipped = 0
    for pk in pks.iterator(chunk_size=batch_size):
        examined += 1
        with transaction.atomic(using=using):
            row = load_row(model, pk, using, lock=not dry_run)
            if row is None:
                skipped += 1
                continue
            check_scope(config, row, ctx)
            if (
                Resource.objects.using(using)
                .filter(
                    model_label=model._meta.label_lower,
                    object_pk=identity(pk),
                    revision__gt=0,
                )
                .exists()
            ):
                skipped += 1
                continue
            pending += 1
            if not dry_run:
                append_event(
                    config,
                    row,
                    using=using,
                    action="baseline",
                    before=None,
                    after=snapshot(config, row),
                    ctx=ctx,
                )
                created += 1
    return BackfillResult(examined, pending, created, skipped, dry_run)


def restore(model, pk, *, revision, expected_revision, using=None):
    """Restore selected scalar fields on a live row; audit a new event, retain history."""
    config = registration(model)
    ctx = current_context()
    using = using or router.db_for_write(model)
    with transaction.atomic(using=using):
        row = load_row(model, pk, using, lock=True)
        if row is None:
            raise HistoryConflict("Deleted/missing rows cannot be resurrected by restore().")
        check_scope(config, row, ctx)
        resource = _resource(model, pk, using)
        if resource is None or resource.revision != expected_revision:
            raise HistoryConflict("Expected revision does not match current history.")
        if not verify_history(model, pk, using=using).valid:
            raise HistoryConflict("History verification failed.")
        events = history(model, pk, using=using)
        if not events or snapshot(config, row) != events[-1].after:
            raise HistoryConflict("Current data differs from the history head; reconcile first.")
        target = next((event for event in events if event.revision == revision), None)
        if target is None or target.after is None:
            raise HistoryConflict("Target revision has no restorable state.")
        if set(target.after) != set(config.fields):
            raise HistoryConflict("Registered fields changed; migrate history explicitly.")
        updates = []
        for name in config.fields:
            field = model._meta.get_field(name)
            if name == config.scope_field or field.is_relation:
                if target.after[name] != snapshot(config, row)[name]:
                    raise UnsupportedOperation("Restoration cannot change scope or relationships.")
                continue
            setattr(row, field.attname, field.to_python(target.after[name]))
            updates.append(name)
        if not updates:
            raise UnsupportedOperation("No scalar fields are registered for restoration.")
        token = _action.set((model, row.pk, "restore"))
        try:
            row.save(using=using, update_fields=updates)
        finally:
            _action.reset(token)
        return row
