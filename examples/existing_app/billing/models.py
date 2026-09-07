from django.conf import settings
from django.db import models

from pyscoped import scoped
from pyscoped.query import ScopedManager, ScopedQuerySet


class Organization(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    name = models.CharField(max_length=100)


class Membership(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)


class InvoiceQuerySet(ScopedQuerySet):
    def pending(self):
        return self.filter(status="draft")


@scoped(scope_field="organization", fields=["amount", "status"])
class Invoice(models.Model):
    # Ordinary pre-existing columns. No pyscoped IDs or copied business objects.
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=32, default="draft")
    private_note = models.TextField(default="")
    objects = ScopedManager.from_queryset(InvoiceQuerySet)()
