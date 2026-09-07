import uuid

from django.db import models

from pyscoped import scoped
from pyscoped.models import ScopedModel
from pyscoped.query import ScopedManager


@scoped(scope_field="tenant", fields=["amount", "status"])
class Invoice(ScopedModel):
    tenant = models.CharField(max_length=64)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=32, default="draft")
    secret = models.TextField(default="not in audit")


@scoped(scope_field="tenant", fields=["name"], mode="audit")
class ExistingModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    tenant = models.CharField(max_length=64)
    name = models.CharField(max_length=64)
    objects = ScopedManager()


@scoped(scope_field="tenant", fields=["value"])
class Child(ScopedModel):
    tenant = models.CharField(max_length=64)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE)
    value = models.IntegerField(default=0)


@scoped(scope_field="tenant", fields=["note"])
class Receipt(ScopedModel):
    tenant = models.CharField(max_length=64)
    invoice = models.OneToOneField(Invoice, on_delete=models.CASCADE)
    note = models.CharField(max_length=64, default="")
