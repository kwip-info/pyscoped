import json
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.db import models

from pyscoped import scope
from pyscoped.exceptions import HistoryConflict, ScopeViolation
from pyscoped.history import backfill, history, restore, verify_history
from pyscoped.models import AuditEvent, Resource
from tests.testapp.models import ExistingModel, Invoice

pytestmark = pytest.mark.django_db


def legacy_invoice(tenant="a", amount=10):
    # Simulates rows present before model registration, with no invented prior audit.
    row = Invoice(tenant=tenant, amount=amount)
    models.QuerySet(model=Invoice).bulk_create([row])
    return row


def test_baseline_dry_run_then_idempotent_apply():
    row = legacy_invoice()
    legacy_invoice("b")
    with scope(actor="importer", scope="a"):
        assert backfill(Invoice).pending == 1
        assert Resource.objects.count() == 0
        result = backfill(Invoice, dry_run=False)
        assert result.created == 1
        assert Invoice.objects.get().pk == row.pk
        assert backfill(Invoice, dry_run=False).skipped == 1
        event = history(Invoice, row.pk)[0]
        assert event.action == "baseline" and event.before is None
        assert verify_history(Invoice, row.pk).valid
    assert AuditEvent.objects.count() == 1


def test_backfill_resumes_after_partial_failure():
    legacy_invoice()
    legacy_invoice()
    original = AuditEvent.save
    calls = 0

    def fail_second(event, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("fail")
        return original(event, *args, **kwargs)

    with scope(actor="importer", scope="a"):
        with patch.object(AuditEvent, "save", fail_second):
            with pytest.raises(RuntimeError):
                backfill(Invoice, dry_run=False)
        assert AuditEvent.objects.count() == 1
        result = backfill(Invoice, dry_run=False)
        assert result.created == 1 and result.skipped == 1


def test_command_defaults_to_preview():
    legacy_invoice()
    output = StringIO()
    call_command("pyscoped_backfill", "testapp.Invoice", actor="importer", scope="a", stdout=output)
    assert json.loads(output.getvalue())["dry_run"] is True
    assert AuditEvent.objects.count() == 0
    call_command(
        "pyscoped_backfill",
        "testapp.Invoice",
        actor="importer",
        scope="a",
        apply=True,
        stdout=StringIO(),
    )
    assert AuditEvent.objects.count() == 1


def test_restore_appends_and_preserves_unselected_fields():
    with scope(actor="a", scope="a"):
        row = Invoice.objects.create(tenant="a", amount=10)
        row.amount = 20
        row.secret = "changed independently"
        row.save()
        restored = restore(Invoice, row.pk, revision=1, expected_revision=2)
        assert restored.amount == 10 and restored.secret == "changed independently"
        events = history(Invoice, row.pk)
        assert [e.action for e in events] == ["create", "update", "restore"]
        assert [e.revision for e in events] == [1, 2, 3]
        assert verify_history(Invoice, row.pk).valid
        with pytest.raises(HistoryConflict):
            restore(Invoice, row.pk, revision=1, expected_revision=2)


def test_restore_fails_on_untracked_drift():
    with scope(actor="a", scope="a"):
        row = Invoice.objects.create(tenant="a", amount=10)
        models.QuerySet(model=Invoice).filter(pk=row.pk).update(amount=99)
        with pytest.raises(HistoryConflict):
            restore(Invoice, row.pk, revision=1, expected_revision=1)
        assert Invoice.objects.get().amount == 99


def test_history_and_restore_always_scope_even_in_audit_mode():
    with scope(actor="a", scope="a"):
        row = ExistingModel.objects.create(tenant="a", name="private")
    with scope(actor="b", scope="b"):
        for operation in [history, verify_history]:
            with pytest.raises(ScopeViolation):
                operation(ExistingModel, row.pk)
        with pytest.raises(ScopeViolation):
            restore(ExistingModel, row.pk, revision=1, expected_revision=1)


def test_history_detects_tampering_and_deleted_tail():
    with scope(actor="a", scope="a"):
        row = Invoice.objects.create(tenant="a", amount=10)
        assert verify_history(Invoice, row.pk).valid
        event = AuditEvent.objects.get()
        models.QuerySet(model=AuditEvent).filter(pk=event.pk).update(actor="forged")
        assert not verify_history(Invoice, row.pk).valid
        models.QuerySet(model=AuditEvent).filter(pk=event.pk).delete()
        assert not verify_history(Invoice, row.pk).valid


def test_delete_retains_history_but_cannot_restore_or_reassign_identity():
    with scope(actor="a", scope="a"):
        row = Invoice.objects.create(tenant="a")
        pk = row.pk
        row.delete()
        assert len(history(Invoice, pk)) == 2
        with pytest.raises(HistoryConflict):
            restore(Invoice, pk, revision=1, expected_revision=2)
    with scope(actor="b", scope="b"):
        with pytest.raises(ScopeViolation):
            Invoice.objects.create(pk=pk, tenant="b")
        assert Invoice.objects.count() == 0


def test_restore_audit_failure_leaves_original_row():
    with scope(actor="a", scope="a"):
        row = Invoice.objects.create(tenant="a", amount=10)
        row.amount = 20
        row.save()
        with patch.object(AuditEvent, "save", side_effect=RuntimeError()):
            with pytest.raises(RuntimeError):
                restore(Invoice, row.pk, revision=1, expected_revision=2)
        assert Invoice.objects.get().amount == 20
        assert len(history(Invoice, row.pk)) == 2
