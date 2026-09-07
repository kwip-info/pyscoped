from unittest.mock import patch

import pytest
from django.db import models
from django.db.models import Count, F
from django.db.models.signals import post_save

from pyscoped import scope
from pyscoped.exceptions import ScopeViolation, UnsupportedOperation
from pyscoped.history import history, verify_history
from pyscoped.models import AuditEvent
from tests.testapp.models import Child, Invoice

pytestmark = pytest.mark.django_db


def test_outer_join_hides_foreign_rows_without_losing_parent():
    with scope(actor="a", scope="a"):
        inv = Invoice.objects.create(tenant="a")
        child = Child.objects.create(tenant="a", invoice=inv, value=17)
        # Simulate inconsistent data written before adoption; ON scoping still hides it.
        models.QuerySet(model=Child).filter(pk=child.pk).update(tenant="b")
        assert Invoice.objects.values("child__value").get()["child__value"] is None
        assert Invoice.objects.annotate(children=Count("child")).get().children == 0
        assert not Invoice.objects.filter(child__value=17).exists()
        assert Invoice.objects.select_related().count() == 1


def test_select_related_cannot_reveal_foreign_parent():
    with scope(actor="a", scope="a"):
        inv = Invoice.objects.create(tenant="a")
        child = Child.objects.create(tenant="a", invoice=inv)
        models.QuerySet(model=Invoice).filter(pk=inv.pk).update(tenant="b")
        assert not list(Child.objects.select_related("invoice"))
        assert not list(Child.objects.values("invoice__amount"))
        # Root access is still scoped. Joined inner relation appropriately removes a bad row.
        assert Child.objects.filter(pk=child.pk).exists()


def test_joined_filter_update_and_delete_only_touch_visible_rows():
    with scope(actor="a", scope="a"):
        inv = Invoice.objects.create(tenant="a", amount=10)
        Child.objects.create(tenant="a", invoice=inv, value=7)
        assert Invoice.objects.filter(child__value=7).update(amount=11) == 1
        assert Invoice.objects.get().amount == 11
        assert Child.objects.filter(invoice__amount=11).delete()[0] == 1
        assert len(history(Invoice, inv.pk)) == 2


def test_bulk_create_and_update_capture_real_values_without_save_signals():
    signals = []

    def observer(sender, instance, **kwargs):
        signals.append(instance.pk)

    post_save.connect(observer, sender=Invoice)
    try:
        with scope(actor="a", scope="a"):
            rows = Invoice.objects.bulk_create(
                [
                    Invoice(tenant="a", amount=1),
                    Invoice(tenant="a", amount=2),
                ],
                batch_size=1,
            )
            assert len(rows) == 2 and all(row.pk for row in rows)
            for row in rows:
                row.amount = F("amount") + 10
            assert Invoice.objects.bulk_update(rows, ["amount"], batch_size=1) == 2
            assert list(Invoice.objects.order_by("amount").values_list("amount", flat=True)) == [
                11,
                12,
            ]
            assert Invoice.objects.update(amount=F("amount") + 1) == 2
            assert signals == []  # native Django bulk/queryset signal behavior retained
            assert AuditEvent.objects.count() == 6
            assert history(Invoice, rows[0].pk)[1].before["amount"] == "1.00"
            assert history(Invoice, rows[0].pk)[1].after["amount"] == "11.00"
            assert verify_history(Invoice, rows[0].pk).valid
    finally:
        post_save.disconnect(observer, sender=Invoice)


def test_bulk_insert_failure_rolls_back_whole_batch():
    with scope(actor="a", scope="a"):
        with patch.object(AuditEvent, "save", side_effect=RuntimeError("audit failed")):
            with pytest.raises(RuntimeError):
                Invoice.objects.bulk_create([Invoice(tenant="a"), Invoice(tenant="a")])
        assert Invoice.objects.count() == 0
        assert AuditEvent.objects.count() == 0


def test_bulk_update_denies_foreign_identity_and_cannot_transfer_scope():
    with scope(actor="b", scope="b"):
        foreign = Invoice.objects.create(tenant="b", amount=10)
    with scope(actor="a", scope="a"):
        own = Invoice.objects.create(tenant="a", amount=1)
        own.amount = foreign.amount = 99
        with pytest.raises(ScopeViolation):
            Invoice.objects.bulk_update([own, foreign], ["amount"])
        assert Invoice.objects.get().amount == 1
        with pytest.raises(UnsupportedOperation):
            Invoice.objects.bulk_update([own], ["tenant"])
        with pytest.raises(ScopeViolation):
            Invoice.objects.bulk_create([Invoice(tenant="b")])


def test_bulk_update_preserves_extra_filters_and_rejects_duplicates():
    with scope(actor="a", scope="a"):
        rows = Invoice.objects.bulk_create(
            [Invoice(tenant="a", amount=1), Invoice(tenant="a", amount=2)]
        )
        for row in rows:
            row.status = "approved"
        assert Invoice.objects.filter(amount=1).bulk_update(rows, ["status"]) == 1
        assert Invoice.objects.filter(status="approved").count() == 1
        with pytest.raises(UnsupportedOperation):
            Invoice.objects.bulk_update([rows[0], rows[0]], ["status"])


@pytest.mark.django_db(transaction=True)
async def test_async_bulk_operations():
    async with scope(actor="a", scope="a"):
        rows = await Invoice.objects.abulk_create([Invoice(tenant="a", amount=1)])
        rows[0].amount = 2
        assert await Invoice.objects.abulk_update(rows, ["amount"]) == 1
        assert (await Invoice.objects.aget()).amount == 2


def test_forward_fk_and_to_attr_prefetch_cannot_leak_foreign_parent():
    from django.db.models import Prefetch

    with scope(actor="a", scope="a"):
        inv = Invoice.objects.create(tenant="a", amount=9)
        child = Child.objects.create(tenant="a", invoice=inv)
        models.QuerySet(model=Invoice).filter(pk=inv.pk).update(tenant="b")
        fresh = Child.objects.get(pk=child.pk)
        with pytest.raises(Invoice.DoesNotExist):
            _ = fresh.invoice
        prefetched = Child.objects.prefetch_related(Prefetch("invoice", to_attr="parent")).get()
        assert prefetched.parent is None


def test_forward_cached_relation_still_checks_current_context():
    with scope(actor="a", scope="a"):
        inv = Invoice.objects.create(tenant="a")
        child = Child.objects.create(tenant="a", invoice=inv)
        assert child.invoice.pk == inv.pk
    with scope(actor="b", scope="b"):
        with pytest.raises(ScopeViolation):
            _ = child.invoice


def test_reverse_one_to_one_is_scoped():
    from tests.testapp.models import Receipt

    with scope(actor="a", scope="a"):
        inv = Invoice.objects.create(tenant="a")
        receipt = Receipt.objects.create(tenant="a", invoice=inv, note="private")
        assert Invoice.objects.get().receipt.pk == receipt.pk
        models.QuerySet(model=Receipt).filter(pk=receipt.pk).update(tenant="b")
        with pytest.raises(Receipt.DoesNotExist):
            _ = Invoice.objects.get().receipt
