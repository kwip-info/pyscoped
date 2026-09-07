import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.test.utils import isolate_apps

from pyscoped import register, scope
from pyscoped.checks import check_registered_models
from pyscoped.exceptions import UnsupportedOperation
from pyscoped.query import ScopedManager, ScopedQuerySet
from pyscoped.registry import Registration
from tests.testapp.models import Invoice


def test_idempotent_registration_and_conflict():
    assert register(Invoice, scope_field="tenant", fields=["amount", "status"]) is Invoice
    with pytest.raises(ImproperlyConfigured):
        register(Invoice, scope_field="tenant", fields=["status"])


@pytest.mark.parametrize(
    "options",
    [
        dict(scope_field="unknown", fields=["amount"]),
        dict(scope_field="tenant", fields=[]),
        dict(scope_field="tenant", fields=["pk"]),
        dict(scope_field="tenant", fields=["amount"], mode="guess"),
    ],
)
def test_bad_registration_fails_before_use(options):
    with pytest.raises(ImproperlyConfigured):
        register(Invoice, **options)


@isolate_apps()
def test_unscoped_manager_check(monkeypatch):
    class Unintegrated(models.Model):
        tenant = models.CharField(max_length=50)
        value = models.IntegerField()

        class Meta:
            app_label = "checks_test"

    config = Registration(Unintegrated, "tenant", ("value",), "enforce")
    monkeypatch.setattr("pyscoped.checks.registered_models", lambda: (config,))
    assert "pyscoped.E001" in {e.id for e in check_registered_models()}
    with pytest.raises(ImproperlyConfigured):
        register(Unintegrated, scope_field="tenant", fields=["value"])


@isolate_apps()
def test_custom_queryset_requires_explicit_composition(monkeypatch):
    class ExistingQuerySet(models.QuerySet):
        def published(self):
            return self.filter(value=1)

    class Unsafe(models.Model):
        tenant = models.CharField(max_length=50)
        value = models.IntegerField()
        objects = ScopedManager.from_queryset(ExistingQuerySet)()

        class Meta:
            app_label = "checks_test"

    config = Registration(Unsafe, "tenant", ("value",), "enforce")
    monkeypatch.setattr("pyscoped.checks.registered_models", lambda: (config,))
    assert "pyscoped.E003" in {e.id for e in check_registered_models()}

    class IntegratedQuerySet(ScopedQuerySet, ExistingQuerySet):
        pass

    assert issubclass(
        ScopedManager.from_queryset(IntegratedQuerySet)._queryset_class, ScopedQuerySet
    )


@pytest.mark.django_db
def test_cached_query_and_iterator_context_escape():
    with scope(actor="a", scope="a"):
        Invoice.objects.create(tenant="a")
        qs = Invoice.objects.all()
        assert len(qs) == 1
        iterator = qs.iterator()
        next(iterator)
    from pyscoped.exceptions import MissingContext

    with pytest.raises(MissingContext):
        list(qs)


@pytest.mark.django_db
def test_audit_events_cannot_update_through_public_methods():
    from pyscoped.models import AuditEvent

    with scope(actor="a", scope="a"):
        Invoice.objects.create(tenant="a")
    event = AuditEvent.objects.get()
    with pytest.raises(UnsupportedOperation):
        event.save()
    with pytest.raises(UnsupportedOperation):
        AuditEvent.objects.update(actor="forged")
    with pytest.raises(UnsupportedOperation):
        AuditEvent.objects.all().delete()
