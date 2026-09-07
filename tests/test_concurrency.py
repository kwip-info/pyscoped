from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections, connection, connections, models
from django.db.models import F

from pyscoped import scope
from pyscoped.history import backfill, history, verify_history
from tests.testapp.models import Invoice

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.postgres]


def run_workers(operation, count=4):
    barrier = Barrier(count)

    def worker(index):
        close_old_connections()
        try:
            with scope(actor=f"worker-{index}", scope="a"):
                barrier.wait(timeout=10)
                operation()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=count) as pool:
        list(pool.map(worker, range(count)))


def test_concurrent_updates_preserve_history_and_values():
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row locks.")
    with scope(actor="setup", scope="a"):
        row = Invoice.objects.create(tenant="a", amount=0)

    def increment():
        for _ in range(5):
            Invoice.objects.filter(pk=row.pk).update(amount=F("amount") + 1)

    run_workers(increment)
    with scope(actor="inspect", scope="a"):
        assert Invoice.objects.get().amount == 20
        events = history(Invoice, row.pk)
        assert len(events) == 21
        assert [event.revision for event in events] == list(range(1, 22))
        for prior, event in zip(events, events[1:]):
            assert event.before == prior.after
        assert verify_history(Invoice, row.pk).valid


def test_concurrent_baseline_is_idempotent():
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row locks.")
    row = Invoice(tenant="a", amount=10)
    models.QuerySet(model=Invoice).bulk_create([row])
    run_workers(lambda: backfill(Invoice, dry_run=False))
    with scope(actor="inspect", scope="a"):
        assert len(history(Invoice, row.pk)) == 1
        assert verify_history(Invoice, row.pk).valid
