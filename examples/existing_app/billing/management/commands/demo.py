from decimal import Decimal

from django.core.management.base import BaseCommand

from pyscoped import scope
from pyscoped.exceptions import ScopeViolation
from pyscoped.history import backfill, history, restore, verify_history

from ...models import Invoice


class Command(BaseCommand):
    help = "Exercise incremental adoption of the existing invoice created by migration 0002."

    def handle(self, *args, **options):
        with scope(actor="migration-operator", scope="acme"):
            invoice = Invoice.objects.get(pk=501)
            assert invoice.amount == Decimal("10.00")
            assert backfill(Invoice).pending == 1
            assert backfill(Invoice, dry_run=False).created == 1
            assert backfill(Invoice, dry_run=False).skipped == 1
        with scope(actor="existing-user-42", scope="acme"):
            assert Invoice.objects.pending().count() == 1
            Invoice.objects.filter(pk=501).update(amount=20, status="approved")
            invoice = restore(Invoice, 501, revision=1, expected_revision=2)
            assert invoice.amount == 10 and invoice.private_note == "retained; never audited"
            assert [e.action for e in history(Invoice, 501)] == ["baseline", "update", "restore"]
            assert verify_history(Invoice, 501).valid
        with scope(actor="another-user", scope="other"):
            assert not Invoice.objects.filter(pk=501).exists()
            try:
                invoice.save()
            except ScopeViolation:
                pass
            else:
                raise AssertionError("Cross-scope instance write was not denied.")
        self.stdout.write(
            self.style.SUCCESS(
                "PASS: existing row 501 retained; baseline, custom queryset, scoped update, "
                "history, restoration, and cross-scope denial verified. No cloud connection."
            )
        )
