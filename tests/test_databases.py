import pytest
from django.db import transaction
from django.db.models import F
from django.test import override_settings

from pyscoped import scope
from pyscoped.history import history, restore
from pyscoped.models import AuditEvent, Resource
from tests.testapp.models import Invoice

pytestmark = pytest.mark.django_db(databases=["default", "other"])


class WriteOtherRouter:
    def db_for_read(self, model, **hints):
        return "default"

    def db_for_write(self, model, **hints):
        return "other"


def test_explicit_alias_keeps_app_audit_and_history_together():
    with scope(actor="a", scope="a"):
        row = Invoice.objects.using("other").create(tenant="a", amount=1)
        Invoice.objects.using("other").update(amount=2)
        restored = restore(Invoice, row.pk, revision=1, expected_revision=2, using="other")
        assert restored.amount == 1
        assert len(history(Invoice, row.pk, using="other")) == 3
        assert Invoice.objects.using("default").count() == 0
        with pytest.raises(RuntimeError):
            with transaction.atomic(using="other"):
                Invoice.objects.using("other").create(tenant="a", amount=3)
                raise RuntimeError()
        assert Invoice.objects.using("other").count() == 1
    assert Resource.objects.using("default").count() == 0
    assert AuditEvent.objects.using("default").count() == 0
    assert AuditEvent.objects.using("other").count() == 3


def test_queryset_mutations_use_write_router_for_whole_transaction():
    with override_settings(DATABASE_ROUTERS=[WriteOtherRouter()]):
        with scope(actor="a", scope="a"):
            Invoice.objects.create(tenant="a", amount=1)
            assert Invoice.objects.update(amount=F("amount") + 1) == 1
            assert Invoice.objects.using("other").get().amount == 2
            assert Invoice.objects.all().delete()[0] == 1
        assert AuditEvent.objects.using("other").count() == 3
        assert AuditEvent.objects.using("default").count() == 0
