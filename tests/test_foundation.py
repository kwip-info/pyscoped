from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import transaction
from django.db.models import F

from pyscoped import current_context, scope
from pyscoped.exceptions import MissingContext, ScopeViolation, UnsupportedOperation
from pyscoped.models import AuditEvent, Resource
from tests.testapp.models import Child, ExistingModel, Invoice

pytestmark = pytest.mark.django_db


def test_base_adds_no_columns():
    assert {f.name for f in Invoice._meta.fields} == {"id", "tenant", "amount", "status", "secret"}


def test_explicit_actor_create_update_delete():
    with scope(actor="alice", scope="a"):
        inv = Invoice.objects.create(tenant="a", amount=10)
        inv.amount = 20
        inv.save()
        inv.delete()
    events = list(AuditEvent.objects.all())
    assert [e.action for e in events] == ["create", "update", "delete"]
    assert [e.revision for e in events] == [1, 2, 3]
    assert {e.actor for e in events} == {"alice"}
    assert events[0].before is None
    assert events[1].before["amount"] == "10.00"
    assert events[1].after["amount"] == "20.00"
    assert events[2].after is None
    assert "secret" not in events[0].after
    assert events[1].previous_digest == events[0].digest


def test_missing_actor_never_saves_or_infers_owner():
    with pytest.raises(MissingContext):
        Invoice(tenant="a", amount=10).save()
    with scope(actor="alice", scope="a"):
        inv = Invoice.objects.create(tenant="a", amount=10)
    inv.amount = 20
    with pytest.raises(MissingContext):
        inv.save()
    assert AuditEvent.objects.count() == 1


def test_scope_filters_and_rejects_instance_updates():
    with scope(actor="alice", scope="a"):
        inv = Invoice.objects.create(tenant="a", amount=10)
    with scope(actor="bob", scope="b"):
        assert Invoice.objects.count() == 0
        with pytest.raises(ScopeViolation):
            inv.save()
        with pytest.raises(ScopeViolation):
            inv.delete()
        with pytest.raises(ScopeViolation):
            Invoice.objects.create(tenant="a", amount=10)
    assert AuditEvent.objects.count() == 1


def test_partial_save_audits_actual_persisted_values():
    with scope(actor="alice", scope="a"):
        inv = Invoice.objects.create(tenant="a", amount=10)
        inv.amount = 99
        inv.status = "approved"
        inv.save(update_fields=["status"])
    event = AuditEvent.objects.last()
    assert event.after == {"amount": "10.00", "status": "approved"}


def test_expression_saves_capture_database_result():
    with scope(actor="alice", scope="a"):
        inv = Invoice.objects.create(tenant="a", amount=10)
        inv.amount = F("amount") + 1
        inv.save(update_fields=["amount"])
    assert AuditEvent.objects.last().after["amount"] == "11.00"


def test_nested_rollback_removes_rows_events_and_heads():
    with scope(actor="alice", scope="a"):
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                Invoice.objects.create(tenant="a", amount=10)
                raise RuntimeError("abort")
        assert Invoice.objects.count() == 0
    assert AuditEvent.objects.count() == 0
    assert Resource.objects.count() == 0


def test_audit_failure_rolls_back_app_write():
    with scope(actor="alice", scope="a"):
        with patch.object(AuditEvent, "save", side_effect=RuntimeError("audit offline")):
            with pytest.raises(RuntimeError):
                Invoice.objects.create(tenant="a", amount=10)
        assert Invoice.objects.count() == 0


def test_uuid_existing_model_audit_mode_keeps_reads():
    with scope(actor="alice", scope="a"):
        row = ExistingModel.objects.create(tenant="a", name="before")
    assert ExistingModel.objects.get(pk=row.pk).name == "before"
    with scope(actor="operator", scope="b"):
        row.name = "after"
        row.save()
    assert AuditEvent.objects.last().scope == "a"
    assert AuditEvent.objects.last().actor == "operator"
    assert Resource.objects.get().object_pk == str(row.pk)


def test_queryset_update_and_delete_audited_and_scoped():
    with scope(actor="bob", scope="b"):
        Invoice.objects.create(tenant="b", amount=50)
    with scope(actor="alice", scope="a"):
        Invoice.objects.create(tenant="a", amount=10)
        assert Invoice.objects.update(amount=F("amount") + 1) == 1
        assert Invoice.objects.get().amount == Decimal("11")
        assert Invoice.objects.all().delete()[0] == 1
    with scope(actor="bob", scope="b"):
        assert Invoice.objects.get().amount == 50
    assert AuditEvent.objects.filter(scope="a").count() == 3


def test_cascade_delete_audits_both_models():
    with scope(actor="alice", scope="a"):
        inv = Invoice.objects.create(tenant="a")
        Child.objects.create(tenant="a", invoice=inv)
        inv.delete()
    assert AuditEvent.objects.filter(action="delete").count() == 2


def test_raw_fixture_and_bulk_conflicts_fail_before_writes():
    with scope(actor="alice", scope="a"):
        with pytest.raises(UnsupportedOperation):
            Invoice(tenant="a").save_base(raw=True)
        with pytest.raises(UnsupportedOperation):
            Invoice.objects.bulk_create([Invoice(tenant="a")], ignore_conflicts=True)
        assert Invoice.objects.count() == 0


def test_context_reset_and_query_cannot_escape():
    with scope(actor="alice", scope="a"):
        ctx = current_context()
        qs = Invoice.objects.all()
        with scope(actor="bob", scope="b"):
            with pytest.raises(ScopeViolation):
                list(qs)
        assert current_context() is ctx
    assert current_context(required=False) is None
    with pytest.raises(MissingContext):
        list(qs)


def test_app_startup_does_not_query_database(django_assert_num_queries):
    from django.apps import apps

    with django_assert_num_queries(0):
        apps.get_app_config("pyscoped").ready()
