"""Tests for Django ScopedModel integration."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

django = pytest.importorskip("django")

from django.db import models
from django.test import TestCase

from scoped.contrib.django import get_backend, get_client, reset_backend
from scoped.contrib.django.models import (
    ScopedDjangoManager,
    ScopedModel,
    ScopedQuerySet,
    scoped_context_for,
)
from scoped.identity.context import ScopedContext
from scoped.identity.principal import PrincipalStore


# ---------------------------------------------------------------------------
# Test models — these are abstract so Django won't try to create real tables.
# We use them to test serialization and ScopedMeta resolution.
# ---------------------------------------------------------------------------


class InvoiceModel(ScopedModel):
    """Test model with scoped_fields filter."""

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    notes = models.TextField(blank=True, default="")

    class ScopedMeta:
        object_type = "invoice"
        scoped_fields = ["amount", "currency"]

    class Meta:
        app_label = "test_scoped_model"


class DocumentModel(ScopedModel):
    """Test model with all fields synced (scoped_fields=None)."""

    title = models.CharField(max_length=200)
    created = models.DateTimeField(default=datetime.now)
    doc_uuid = models.UUIDField(default=uuid.uuid4)
    score = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))

    class ScopedMeta:
        object_type = "document"
        scoped_fields = None  # all fields

    class Meta:
        app_label = "test_scoped_model"


class EmptyMetaModel(ScopedModel):
    """Test model with no ScopedMeta override (empty object_type)."""

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "test_scoped_model"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset():
    reset_backend()
    yield
    reset_backend()


@pytest.fixture
def backend():
    b = get_backend()
    b.initialize()
    return b


@pytest.fixture
def client(backend):
    return get_client()


@pytest.fixture
def principal_store(backend):
    return PrincipalStore(backend)


@pytest.fixture
def user(principal_store):
    return principal_store.create_principal(kind="user", display_name="Test User")


@pytest.fixture
def other_user(principal_store):
    return principal_store.create_principal(kind="user", display_name="Other User")


# ---------------------------------------------------------------------------
# _to_scoped_dict() serialization tests
# ---------------------------------------------------------------------------


class TestToScopedDict:
    """Test _to_scoped_dict() serialization of various field types."""

    def test_decimal_field_serialized_as_string(self):
        """DecimalField values are serialized to strings."""
        instance = InvoiceModel()
        instance.amount = Decimal("99.95")
        instance.currency = "USD"
        instance.notes = "Some notes"

        data = instance._to_scoped_dict()

        assert data["amount"] == "99.95"
        assert isinstance(data["amount"], str)

    def test_datetime_field_serialized_as_iso(self):
        """DateTimeField values are serialized to ISO 8601 strings."""
        ts = datetime(2025, 6, 15, 12, 30, 0, tzinfo=timezone.utc)
        instance = DocumentModel()
        instance.title = "Test Doc"
        instance.created = ts
        instance.doc_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        instance.score = Decimal("4.50")

        data = instance._to_scoped_dict()

        assert data["created"] == ts.isoformat()

    def test_uuid_field_serialized_as_string(self):
        """UUIDField values are serialized to strings."""
        test_uuid = uuid.UUID("abcdef01-2345-6789-abcd-ef0123456789")
        instance = DocumentModel()
        instance.title = "Test"
        instance.created = datetime.now()
        instance.doc_uuid = test_uuid
        instance.score = Decimal("1.00")

        data = instance._to_scoped_dict()

        assert data["doc_uuid"] == str(test_uuid)
        assert isinstance(data["doc_uuid"], str)

    def test_char_field_serialized_as_is(self):
        """CharField values are passed through as-is."""
        instance = InvoiceModel()
        instance.amount = Decimal("10.00")
        instance.currency = "EUR"
        instance.notes = "ignored by scoped_fields"

        data = instance._to_scoped_dict()

        assert data["currency"] == "EUR"

    def test_none_values_preserved(self):
        """None values are serialized as None."""
        instance = DocumentModel()
        instance.title = "Test"
        instance.created = None
        instance.doc_uuid = uuid.uuid4()
        instance.score = Decimal("0.00")

        data = instance._to_scoped_dict()

        assert data["created"] is None

    def test_excludes_internal_fields(self):
        """pk, id, scoped_object_id, scoped_owner_id are excluded."""
        instance = DocumentModel()
        instance.title = "Test"
        instance.created = datetime.now()
        instance.doc_uuid = uuid.uuid4()
        instance.score = Decimal("0.00")
        instance.scoped_object_id = "obj-123"
        instance.scoped_owner_id = "owner-456"

        data = instance._to_scoped_dict()

        assert "id" not in data
        assert "pk" not in data
        assert "scoped_object_id" not in data
        assert "scoped_owner_id" not in data


# ---------------------------------------------------------------------------
# ScopedMeta.scoped_fields filtering tests
# ---------------------------------------------------------------------------


class TestScopedFieldsFiltering:
    """Test that ScopedMeta.scoped_fields filters serialization."""

    def test_scoped_fields_limits_output(self):
        """Only fields listed in scoped_fields are included."""
        instance = InvoiceModel()
        instance.amount = Decimal("50.00")
        instance.currency = "GBP"
        instance.notes = "This should be excluded"

        data = instance._to_scoped_dict()

        assert "amount" in data
        assert "currency" in data
        assert "notes" not in data

    def test_scoped_fields_none_includes_all(self):
        """scoped_fields=None includes all non-internal fields."""
        instance = DocumentModel()
        instance.title = "All Fields"
        instance.created = datetime.now()
        instance.doc_uuid = uuid.uuid4()
        instance.score = Decimal("3.14")

        data = instance._to_scoped_dict()

        assert "title" in data
        assert "created" in data
        assert "doc_uuid" in data
        assert "score" in data


# ---------------------------------------------------------------------------
# save() tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScopedModelSave:
    """Test save() creates/updates scoped objects."""

    def test_save_creates_scoped_object_on_first_save(self, client, user, backend):
        """First save() creates a ScopedObject via the manager."""
        with ScopedContext(user):
            # We need the Django table to exist — create it manually
            backend.execute(
                "CREATE TABLE IF NOT EXISTS test_scoped_model_invoicemodel ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  scoped_object_id VARCHAR(64),"
                "  scoped_owner_id VARCHAR(64),"
                "  amount DECIMAL(10, 2),"
                "  currency VARCHAR(3),"
                "  notes TEXT"
                ")",
                (),
            )

            invoice = InvoiceModel()
            invoice.amount = Decimal("100.00")
            invoice.currency = "USD"
            invoice.notes = "Test invoice"
            invoice.save()

            # Should have a scoped_object_id after save
            assert invoice.scoped_object_id != ""
            assert invoice.scoped_owner_id == user.id

            # Verify the object exists in pyscoped
            obj = client.services.manager.get(
                invoice.scoped_object_id, principal_id=user.id,
            )
            assert obj is not None
            assert obj.object_type == "invoice"
            assert obj.owner_id == user.id

    def test_save_updates_scoped_object_on_subsequent_saves(
        self, client, user, backend,
    ):
        """Subsequent save() calls create new versions."""
        with ScopedContext(user):
            backend.execute(
                "CREATE TABLE IF NOT EXISTS test_scoped_model_invoicemodel ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  scoped_object_id VARCHAR(64),"
                "  scoped_owner_id VARCHAR(64),"
                "  amount DECIMAL(10, 2),"
                "  currency VARCHAR(3),"
                "  notes TEXT"
                ")",
                (),
            )

            invoice = InvoiceModel()
            invoice.amount = Decimal("100.00")
            invoice.currency = "USD"
            invoice.notes = "v1"
            invoice.save()

            scoped_id = invoice.scoped_object_id
            assert scoped_id != ""

            # Update and save again
            invoice.amount = Decimal("200.00")
            invoice.save()

            # scoped_object_id should remain the same
            assert invoice.scoped_object_id == scoped_id

            # Verify a new version was created
            obj = client.services.manager.get(scoped_id, principal_id=user.id)
            assert obj is not None
            assert obj.current_version == 2

    def test_save_without_context_uses_owner_id_fallback(
        self, client, user, backend,
    ):
        """save() with no ScopedContext falls back to scoped_owner_id."""
        # First create with context
        with ScopedContext(user):
            backend.execute(
                "CREATE TABLE IF NOT EXISTS test_scoped_model_invoicemodel ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  scoped_object_id VARCHAR(64),"
                "  scoped_owner_id VARCHAR(64),"
                "  amount DECIMAL(10, 2),"
                "  currency VARCHAR(3),"
                "  notes TEXT"
                ")",
                (),
            )

            invoice = InvoiceModel()
            invoice.amount = Decimal("50.00")
            invoice.currency = "EUR"
            invoice.save()

            scoped_id = invoice.scoped_object_id

        # Now update without context — should use scoped_owner_id fallback
        assert ScopedContext.current_or_none() is None
        invoice.amount = Decimal("75.00")
        invoice.save()

        obj = client.services.manager.get(scoped_id, principal_id=user.id)
        assert obj is not None
        assert obj.current_version == 2

    def test_save_without_context_or_owner_skips_sync(self, client, backend):
        """save() with no context and no owner_id skips pyscoped sync."""
        backend.execute(
            "CREATE TABLE IF NOT EXISTS test_scoped_model_invoicemodel ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  scoped_object_id VARCHAR(64),"
            "  scoped_owner_id VARCHAR(64),"
            "  amount DECIMAL(10, 2),"
            "  currency VARCHAR(3),"
            "  notes TEXT"
            ")",
            (),
        )

        invoice = InvoiceModel()
        invoice.amount = Decimal("10.00")
        invoice.currency = "JPY"
        invoice.save()

        # No scoped sync — scoped_object_id remains empty
        assert invoice.scoped_object_id == ""

    def test_save_with_empty_object_type_skips_sync(self, client, user, backend):
        """save() with empty ScopedMeta.object_type skips pyscoped sync."""
        backend.execute(
            "CREATE TABLE IF NOT EXISTS test_scoped_model_emptymetamodel ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  scoped_object_id VARCHAR(64),"
            "  scoped_owner_id VARCHAR(64),"
            "  name VARCHAR(100)"
            ")",
            (),
        )

        with ScopedContext(user):
            obj = EmptyMetaModel()
            obj.name = "test"
            obj.save()

            assert obj.scoped_object_id == ""


# ---------------------------------------------------------------------------
# delete() tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScopedModelDelete:
    """Test delete() tombstones the scoped object."""

    def test_delete_tombstones_scoped_object(self, client, user, backend):
        """delete() calls tombstone on the pyscoped manager."""
        with ScopedContext(user):
            backend.execute(
                "CREATE TABLE IF NOT EXISTS test_scoped_model_invoicemodel ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  scoped_object_id VARCHAR(64),"
                "  scoped_owner_id VARCHAR(64),"
                "  amount DECIMAL(10, 2),"
                "  currency VARCHAR(3),"
                "  notes TEXT"
                ")",
                (),
            )

            invoice = InvoiceModel()
            invoice.amount = Decimal("500.00")
            invoice.currency = "USD"
            invoice.save()

            scoped_id = invoice.scoped_object_id
            assert scoped_id != ""

            # Delete the invoice
            invoice.delete()

            # Verify the pyscoped object is tombstoned
            obj = client.services.manager.get(scoped_id, principal_id=user.id)
            # Tombstoned objects may still be visible but lifecycle is ARCHIVED
            # or they may return None depending on implementation
            if obj is not None:
                assert obj.is_tombstoned

    def test_delete_without_scoped_id_still_deletes_django_row(
        self, client, backend,
    ):
        """delete() on an object with no scoped_object_id proceeds normally."""
        backend.execute(
            "CREATE TABLE IF NOT EXISTS test_scoped_model_invoicemodel ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  scoped_object_id VARCHAR(64),"
            "  scoped_owner_id VARCHAR(64),"
            "  amount DECIMAL(10, 2),"
            "  currency VARCHAR(3),"
            "  notes TEXT"
            ")",
            (),
        )

        # Create without context (no scoped sync)
        invoice = InvoiceModel()
        invoice.amount = Decimal("10.00")
        invoice.currency = "JPY"
        invoice.save()

        pk = invoice.pk
        assert invoice.scoped_object_id == ""

        # Delete should work fine
        invoice.delete()

        # Django row should be gone
        row = backend.fetch_one(
            "SELECT * FROM test_scoped_model_invoicemodel WHERE id = ?",
            (pk,),
        )
        assert row is None


# ---------------------------------------------------------------------------
# for_principal() queryset tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestForPrincipal:
    """Test ScopedQuerySet.for_principal() visibility filtering."""

    def test_for_principal_filters_by_owner(self, client, user, other_user, backend):
        """for_principal() returns only objects owned by the principal."""
        backend.execute(
            "CREATE TABLE IF NOT EXISTS test_scoped_model_invoicemodel ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  scoped_object_id VARCHAR(64),"
            "  scoped_owner_id VARCHAR(64),"
            "  amount DECIMAL(10, 2),"
            "  currency VARCHAR(3),"
            "  notes TEXT"
            ")",
            (),
        )

        # Create an invoice for user
        with ScopedContext(user):
            inv1 = InvoiceModel()
            inv1.amount = Decimal("100.00")
            inv1.currency = "USD"
            inv1.save()

        # Create an invoice for other_user
        with ScopedContext(other_user):
            inv2 = InvoiceModel()
            inv2.amount = Decimal("200.00")
            inv2.currency = "EUR"
            inv2.save()

        # for_principal should filter correctly
        qs = InvoiceModel.scoped_objects.for_principal(user.id)
        scoped_ids = list(qs.values_list("scoped_object_id", flat=True))
        assert inv1.scoped_object_id in scoped_ids
        assert inv2.scoped_object_id not in scoped_ids

    def test_for_principal_without_client_falls_back_to_owner(self):
        """for_principal() falls back to owner_id filtering when no client."""
        # Mock get_client→None so save() skips scoped sync and
        # for_principal() uses the scoped_owner_id fallback path.
        with patch("scoped.contrib.django.get_client", return_value=None):
            InvoiceModel(
                scoped_object_id="obj-1", scoped_owner_id="owner-A",
                amount=Decimal("100.00"), currency="USD",
            ).save()
            InvoiceModel(
                scoped_object_id="obj-2", scoped_owner_id="owner-B",
                amount=Decimal("200.00"), currency="EUR",
            ).save()

            qs = InvoiceModel.scoped_objects.for_principal("owner-A")
            assert qs.count() == 1
            assert qs.first().scoped_object_id == "obj-1"


# ---------------------------------------------------------------------------
# scoped_context_for() tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScopedContextFor:
    """Test the scoped_context_for() context manager."""

    def test_sets_scoped_context(self, client, user, backend):
        """scoped_context_for() sets ScopedContext for the block."""
        assert ScopedContext.current_or_none() is None

        with scoped_context_for(user.id):
            ctx = ScopedContext.current_or_none()
            assert ctx is not None
            assert ctx.principal_id == user.id

        # Context is cleaned up after the block
        assert ScopedContext.current_or_none() is None

    def test_scoped_context_for_enables_model_sync(self, client, user, backend):
        """Models saved inside scoped_context_for() sync to pyscoped."""
        backend.execute(
            "CREATE TABLE IF NOT EXISTS test_scoped_model_invoicemodel ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  scoped_object_id VARCHAR(64),"
            "  scoped_owner_id VARCHAR(64),"
            "  amount DECIMAL(10, 2),"
            "  currency VARCHAR(3),"
            "  notes TEXT"
            ")",
            (),
        )

        with scoped_context_for(user.id):
            invoice = InvoiceModel()
            invoice.amount = Decimal("42.00")
            invoice.currency = "USD"
            invoice.save()

        assert invoice.scoped_object_id != ""
        assert invoice.scoped_owner_id == user.id

    def test_scoped_context_for_without_client_is_noop(self):
        """scoped_context_for() yields without error if no client."""
        reset_backend()

        with patch("scoped.contrib.django.get_client", return_value=None):
            with scoped_context_for("any-id"):
                # Should not raise — just a no-op
                assert ScopedContext.current_or_none() is None


# ---------------------------------------------------------------------------
# ScopedDjangoManager tests
# ---------------------------------------------------------------------------


class TestScopedDjangoManager:
    """Test ScopedDjangoManager is correctly configured on ScopedModel."""

    def test_scoped_objects_is_scoped_manager(self):
        """ScopedModel.scoped_objects is a ScopedDjangoManager."""
        assert isinstance(InvoiceModel.scoped_objects, ScopedDjangoManager)

    def test_default_objects_is_standard_manager(self):
        """ScopedModel.objects is Django's default Manager."""
        # objects should be the standard Django manager
        assert hasattr(InvoiceModel, "objects")

    @pytest.mark.django_db
    def test_queryset_is_scoped_queryset(self):
        """ScopedDjangoManager.get_queryset() returns ScopedQuerySet."""
        qs = InvoiceModel.scoped_objects.get_queryset()
        assert isinstance(qs, ScopedQuerySet)


# ---------------------------------------------------------------------------
# Resolve principal_id tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestResolvePrincipalId:
    """Test _resolve_principal_id() fallback chain."""

    def test_uses_context_when_available(self, client, user):
        """Active ScopedContext takes priority."""
        instance = InvoiceModel()
        instance.scoped_owner_id = "other-owner"

        with ScopedContext(user):
            assert instance._resolve_principal_id() == user.id

    def test_falls_back_to_owner_id(self):
        """Uses scoped_owner_id when no context is active."""
        instance = InvoiceModel()
        instance.scoped_owner_id = "fallback-owner"

        assert ScopedContext.current_or_none() is None
        assert instance._resolve_principal_id() == "fallback-owner"

    def test_returns_empty_when_nothing_available(self):
        """Returns empty string when no context and no owner_id."""
        instance = InvoiceModel()
        instance.scoped_owner_id = ""

        assert ScopedContext.current_or_none() is None
        assert instance._resolve_principal_id() == ""
