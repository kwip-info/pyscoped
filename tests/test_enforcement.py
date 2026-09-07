import asyncio
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import models, transaction
from django.db.models import F, Prefetch, Subquery, Sum

from pyscoped import current_context, scope
from pyscoped.exceptions import ScopeViolation, UnsupportedOperation
from pyscoped.models import AuditEvent
from tests.testapp.models import Child, Invoice

pytestmark = pytest.mark.django_db


def seed():
    with scope(actor="a", scope="a"):
        Invoice.objects.create(tenant="a", amount=1)
        inv = Invoice.objects.create(tenant="a", amount=2)
    with scope(actor="b", scope="b"):
        Invoice.objects.create(tenant="b", amount=100)
    return inv


@pytest.mark.parametrize(
    "evaluate",
    [
        list,
        lambda q: q.count(),
        lambda q: q.exists(),
        lambda q: list(q.iterator()),
        lambda q: q.aggregate(total=Sum("amount")),
    ],
)
def test_lazy_evaluation_rejects_changed_context(evaluate):
    seed()
    with scope(actor="a", scope="a"):
        qs = Invoice.objects.all()
    with scope(actor="b", scope="b"):
        with pytest.raises(ScopeViolation):
            evaluate(qs)


def test_scope_applies_before_pagination_and_aggregate():
    seed()
    with scope(actor="a", scope="a"):
        assert Invoice.objects.aggregate(total=Sum("amount")) == {"total": Decimal("3")}
        assert list(Invoice.objects.order_by("-amount").values_list("amount", flat=True)[:1]) == [2]
        assert Invoice.objects.values("status").annotate(total=Sum("amount")).get()["total"] == 3


@pytest.mark.parametrize(
    "combine",
    [
        lambda a, b: a | b,
        lambda a, b: a & b,
        lambda a, b: a ^ b,
        lambda a, b: a.union(b),
        lambda a, b: a.intersection(b),
        lambda a, b: a.difference(b),
    ],
)
def test_cross_scope_query_combinations_rejected(combine):
    seed()
    with scope(actor="b", scope="b"):
        foreign = Invoice.objects.all()
    with scope(actor="a", scope="a"):
        with pytest.raises(ScopeViolation):
            list(combine(Invoice.objects.all(), foreign))


def test_same_scope_union_works():
    seed()
    with scope(actor="a", scope="a"):
        a = Invoice.objects.filter(amount=1)
        b = Invoice.objects.filter(amount=2)
        assert a.union(b).count() == 2
        assert (a | b).count() == 2


def test_foreign_context_subquery_rejected():
    seed()
    with scope(actor="b", scope="b"):
        foreign = Invoice.objects.values("amount")[:1]
    with scope(actor="a", scope="a"):
        with pytest.raises(ScopeViolation):
            list(Invoice.objects.annotate(other_amount=Subquery(foreign)))


def test_same_context_subquery_works():
    seed()
    with scope(actor="a", scope="a"):
        q = Invoice.objects.order_by("amount").values("amount")[:1]
        assert Invoice.objects.annotate(low=Subquery(q)).first().low == 1


def test_update_is_atomic_if_later_audit_fails():
    seed()
    original = AuditEvent.save
    calls = 0

    def fail_second(event, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second audit fails")
        return original(event, *args, **kwargs)

    with scope(actor="a", scope="a"):
        with patch.object(AuditEvent, "save", fail_second):
            with pytest.raises(RuntimeError):
                Invoice.objects.update(amount=F("amount") + 10)
        assert list(Invoice.objects.order_by("amount").values_list("amount", flat=True)) == [1, 2]
    assert AuditEvent.objects.count() == 3


def test_get_and_update_or_create():
    with scope(actor="a", scope="a"):
        inv, created = Invoice.objects.get_or_create(tenant="a", status="draft")
        assert created
        same, created = Invoice.objects.get_or_create(tenant="a", status="draft")
        assert same.pk == inv.pk and not created
        Invoice.objects.update_or_create(pk=inv.pk, defaults={"amount": 9})
        assert Invoice.objects.get().amount == 9
    assert [e.action for e in AuditEvent.objects.all()] == ["create", "update"]


def test_deferred_load_and_refresh_require_correct_scope():
    inv = seed()
    with scope(actor="a", scope="a"):
        deferred = Invoice.objects.only("id", "tenant").get(pk=inv.pk)
    with scope(actor="b", scope="b"):
        with pytest.raises(ScopeViolation):
            _ = deferred.amount
        with pytest.raises(ScopeViolation):
            inv.refresh_from_db()


def test_context_decorator_restores_even_after_error():
    @scope(actor="a", scope="a")
    def fail():
        assert current_context().actor == "a"
        raise RuntimeError()

    with pytest.raises(RuntimeError):
        fail()
    assert current_context(required=False) is None


def test_reject_cross_scope_relationship():
    inv = seed()
    with scope(actor="b", scope="b"):
        with pytest.raises(ScopeViolation):
            Child.objects.create(tenant="b", invoice=inv)
        assert Child.objects.count() == 0


def test_scoped_join_and_prefetch_supported():
    with scope(actor="a", scope="a"):
        inv = Invoice.objects.create(tenant="a")
        Child.objects.create(tenant="a", invoice=inv)
        assert Child.objects.select_related("invoice").get().invoice.pk == inv.pk
        assert Invoice.objects.values("child__value").get()["child__value"] == 0
        loaded = Invoice.objects.prefetch_related(
            Prefetch("child_set", queryset=Child.objects.all())
        )
        assert len(loaded.get().child_set.all()) == 1


def test_historical_cross_scope_cascade_fails_and_rolls_back():
    # Simulate a pre-existing bad FK using an explicitly out-of-contract raw ORM write.
    inv = seed()
    with scope(actor="a", scope="a"):
        child = Child.objects.create(tenant="a", invoice=inv)
        models.QuerySet(model=Child).filter(pk=child.pk).update(tenant="b")
        with pytest.raises(ScopeViolation):
            with transaction.atomic():
                inv.delete()
        assert Invoice.objects.filter(pk=inv.pk).exists()
    assert not AuditEvent.objects.filter(action="delete").exists()


@pytest.mark.parametrize(
    "operation",
    [
        lambda: Invoice.objects.bulk_create([Invoice(tenant="a")], ignore_conflicts=True),
        lambda: Invoice.objects.bulk_update([], ["tenant"]),
        lambda: Invoice.objects.raw("SELECT * FROM testapp_invoice"),
        lambda: Invoice.objects.extra(select={"amount2": "amount"}),
        lambda: Invoice.objects.update(tenant="b"),
        lambda: Invoice.objects.update(pk=7),
    ],
)
def test_unsupported_operations_clear_failure(operation):
    with scope(actor="a", scope="a"):
        with pytest.raises(UnsupportedOperation):
            operation()
        assert Invoice.objects.count() == 0


@pytest.mark.django_db(transaction=True)
async def test_async_crud_and_context_isolation():
    async with scope(actor="a", scope="a"):
        inv = await Invoice.objects.acreate(tenant="a", amount=10)
        await Invoice.objects.aupdate(amount=11)
        assert await Invoice.objects.acount() == 1
        await inv.arefresh_from_db()
        assert inv.amount == 11
        assert len([item async for item in Invoice.objects.aiterator()]) == 1
        await inv.adelete()
    assert current_context(required=False) is None

    async def observe(key):
        async with scope(actor=key, scope=key):
            await asyncio.sleep(0)
            return current_context().actor

    assert await asyncio.gather(observe("a"), observe("b")) == ["a", "b"]


async def test_async_context_decorator():
    @scope(actor="a", scope="a")
    async def actor():
        await asyncio.sleep(0)
        return current_context().actor

    assert await actor() == "a"
    assert current_context(required=False) is None
