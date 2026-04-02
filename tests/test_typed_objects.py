"""Tests for the Typed Object Protocol (P1-2)."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from scoped._type_adapters import (
    DataclassAdapter,
    PydanticAdapter,
    ScopedSerializableAdapter,
    detect_adapter,
)
from scoped._type_registry import TypeRegistry, _registry
from scoped.objects.models import ObjectVersion, compute_checksum
from scoped.storage.sqlite import SQLiteBackend
from scoped.types import ScopedSerializable


# ---------------------------------------------------------------------------
# Test types
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class InvoiceDC:
    """Dataclass test type."""

    amount: float
    currency: str
    status: str = "draft"


class InvoiceCustom:
    """Plain class implementing ScopedSerializable protocol."""

    def __init__(self, amount: float, currency: str):
        self.amount = amount
        self.currency = currency

    def to_scoped_dict(self) -> dict[str, Any]:
        return {"amount": self.amount, "currency": self.currency}

    @classmethod
    def from_scoped_dict(cls, data: dict[str, Any]) -> InvoiceCustom:
        return cls(amount=data["amount"], currency=data["currency"])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_registry():
    """Reset the global type registry between tests."""
    _registry.clear()
    yield
    _registry.clear()


@pytest.fixture
def registry():
    return TypeRegistry()


@pytest.fixture
def sqlite_backend():
    backend = SQLiteBackend(":memory:")
    backend.initialize()
    return backend


# ---------------------------------------------------------------------------
# Adapter detection
# ---------------------------------------------------------------------------


class TestAdapterDetection:
    def test_detects_dataclass(self):
        adapter = detect_adapter(InvoiceDC)
        assert isinstance(adapter, DataclassAdapter)

    def test_detects_scoped_serializable(self):
        adapter = detect_adapter(InvoiceCustom)
        assert isinstance(adapter, ScopedSerializableAdapter)

    def test_raises_for_plain_class(self):
        class PlainClass:
            pass

        with pytest.raises(TypeError, match="Cannot auto-detect"):
            detect_adapter(PlainClass)

    def test_detects_pydantic(self):
        pydantic = pytest.importorskip("pydantic")

        class MyModel(pydantic.BaseModel):
            name: str

        adapter = detect_adapter(MyModel)
        assert isinstance(adapter, PydanticAdapter)


# ---------------------------------------------------------------------------
# TypeRegistry
# ---------------------------------------------------------------------------


class TestTypeRegistry:
    def test_register_and_has_type(self, registry):
        registry.register("invoice", InvoiceDC)
        assert registry.has_type("invoice")
        assert not registry.has_type("unknown")

    def test_get_type(self, registry):
        registry.register("invoice", InvoiceDC)
        assert registry.get_type("invoice") is InvoiceDC
        assert registry.get_type("unknown") is None

    def test_serialize_dataclass(self, registry):
        registry.register("invoice", InvoiceDC)
        inv = InvoiceDC(amount=500, currency="USD")
        result = registry.serialize("invoice", inv)
        assert result == {"amount": 500, "currency": "USD", "status": "draft"}

    def test_deserialize_dataclass(self, registry):
        registry.register("invoice", InvoiceDC)
        data = {"amount": 500, "currency": "USD", "status": "sent"}
        result = registry.deserialize("invoice", data)
        assert isinstance(result, InvoiceDC)
        assert result.amount == 500
        assert result.status == "sent"

    def test_serialize_custom(self, registry):
        registry.register("invoice", InvoiceCustom)
        inv = InvoiceCustom(amount=100, currency="EUR")
        result = registry.serialize("invoice", inv)
        assert result == {"amount": 100, "currency": "EUR"}

    def test_deserialize_custom(self, registry):
        registry.register("invoice", InvoiceCustom)
        data = {"amount": 100, "currency": "EUR"}
        result = registry.deserialize("invoice", data)
        assert isinstance(result, InvoiceCustom)
        assert result.amount == 100

    def test_deserialize_unregistered_returns_dict(self, registry):
        data = {"key": "value"}
        result = registry.deserialize("unknown", data)
        assert result is data  # same dict object

    def test_serialize_pydantic(self, registry):
        pydantic = pytest.importorskip("pydantic")

        class Invoice(pydantic.BaseModel):
            amount: float
            currency: str

        registry.register("invoice", Invoice)
        inv = Invoice(amount=42.5, currency="GBP")
        result = registry.serialize("invoice", inv)
        assert result == {"amount": 42.5, "currency": "GBP"}

    def test_deserialize_pydantic(self, registry):
        pydantic = pytest.importorskip("pydantic")

        class Invoice(pydantic.BaseModel):
            amount: float
            currency: str

        registry.register("invoice", Invoice)
        result = registry.deserialize("invoice", {"amount": 42.5, "currency": "GBP"})
        assert isinstance(result, Invoice)
        assert result.amount == 42.5

    def test_clear(self, registry):
        registry.register("invoice", InvoiceDC)
        registry.clear()
        assert not registry.has_type("invoice")


# ---------------------------------------------------------------------------
# ObjectVersion.typed_data
# ---------------------------------------------------------------------------


class TestObjectVersionTypedData:
    def test_typed_data_with_registered_type(self):
        _registry.register("invoice", InvoiceDC)
        ver = ObjectVersion(
            id="v1",
            object_id="obj1",
            version=1,
            data={"amount": 500, "currency": "USD", "status": "draft"},
            created_at=__import__("datetime").datetime.now(),
            created_by="alice",
            _object_type="invoice",
        )
        result = ver.typed_data
        assert isinstance(result, InvoiceDC)
        assert result.amount == 500

    def test_typed_data_without_registered_type(self):
        data = {"amount": 500}
        ver = ObjectVersion(
            id="v1",
            object_id="obj1",
            version=1,
            data=data,
            created_at=__import__("datetime").datetime.now(),
            created_by="alice",
            _object_type="unknown",
        )
        assert ver.typed_data is data  # falls back to raw dict

    def test_typed_data_without_object_type(self):
        data = {"amount": 500}
        ver = ObjectVersion(
            id="v1",
            object_id="obj1",
            version=1,
            data=data,
            created_at=__import__("datetime").datetime.now(),
            created_by="alice",
        )
        assert ver.typed_data is data  # no _object_type = fallback


# ---------------------------------------------------------------------------
# End-to-end with ScopedManager
# ---------------------------------------------------------------------------


def _create_principal(backend, principal_id: str = "alice") -> str:
    """Create a principal via PrincipalStore for proper FK setup."""
    from scoped.identity.principal import PrincipalStore

    store = PrincipalStore(backend)
    p = store.create_principal(
        kind="user", display_name=principal_id, principal_id=principal_id,
    )
    return p.id


class TestManagerTypedData:
    def test_create_with_dataclass(self, sqlite_backend):
        from scoped.audit.writer import AuditWriter
        from scoped.objects.manager import ScopedManager

        _create_principal(sqlite_backend, "alice")
        _registry.register("invoice", InvoiceDC)

        writer = AuditWriter(sqlite_backend)
        mgr = ScopedManager(sqlite_backend, audit_writer=writer)

        inv = InvoiceDC(amount=500, currency="USD")
        obj, ver = mgr.create(
            object_type="invoice",
            owner_id="alice",
            data=inv,
        )

        # Data was serialized to dict
        assert ver.data == {"amount": 500, "currency": "USD", "status": "draft"}
        # typed_data reconstructs
        assert isinstance(ver.typed_data, InvoiceDC)
        assert ver.typed_data.amount == 500
        # Checksum is valid
        assert ver.checksum == compute_checksum(ver.data)

    def test_create_with_dict_still_works(self, sqlite_backend):
        from scoped.audit.writer import AuditWriter
        from scoped.objects.manager import ScopedManager

        _create_principal(sqlite_backend, "alice")
        writer = AuditWriter(sqlite_backend)
        mgr = ScopedManager(sqlite_backend, audit_writer=writer)

        obj, ver = mgr.create(
            object_type="invoice",
            owner_id="alice",
            data={"amount": 500},
        )
        assert ver.data == {"amount": 500}

    def test_create_with_unregistered_type_raises(self, sqlite_backend):
        from scoped.objects.manager import ScopedManager

        _create_principal(sqlite_backend, "alice")
        mgr = ScopedManager(sqlite_backend)

        with pytest.raises(TypeError, match="must be a dict"):
            mgr.create(
                object_type="invoice",
                owner_id="alice",
                data=InvoiceDC(amount=500, currency="USD"),
            )

    def test_create_with_pydantic(self, sqlite_backend):
        pydantic = pytest.importorskip("pydantic")

        class Invoice(pydantic.BaseModel):
            amount: float
            currency: str

        _registry.register("invoice", Invoice)

        from scoped.audit.writer import AuditWriter
        from scoped.objects.manager import ScopedManager

        _create_principal(sqlite_backend, "alice")
        writer = AuditWriter(sqlite_backend)
        mgr = ScopedManager(sqlite_backend, audit_writer=writer)

        inv = Invoice(amount=99.9, currency="EUR")
        obj, ver = mgr.create(
            object_type="invoice", owner_id="alice", data=inv,
        )

        assert ver.data == {"amount": 99.9, "currency": "EUR"}
        restored = ver.typed_data
        assert isinstance(restored, Invoice)
        assert restored.amount == 99.9

    def test_update_with_typed_data(self, sqlite_backend):
        from scoped.audit.writer import AuditWriter
        from scoped.objects.manager import ScopedManager

        _create_principal(sqlite_backend, "alice")
        _registry.register("invoice", InvoiceDC)

        writer = AuditWriter(sqlite_backend)
        mgr = ScopedManager(sqlite_backend, audit_writer=writer)

        obj, v1 = mgr.create(
            object_type="invoice",
            owner_id="alice",
            data=InvoiceDC(amount=500, currency="USD"),
        )

        updated_inv = InvoiceDC(amount=600, currency="USD", status="sent")
        obj2, v2 = mgr.update(
            obj.id,
            principal_id="alice",
            data=updated_inv,
        )

        assert v2.data == {"amount": 600, "currency": "USD", "status": "sent"}
        assert v2.typed_data.amount == 600
        assert v2.version == 2
