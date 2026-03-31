"""Tests for OpenTelemetry instrumentation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scoped.identity.principal import PrincipalStore
from scoped.manifest._services import build_services
from scoped.storage.sqlite import SQLiteBackend


@pytest.fixture
def services():
    backend = SQLiteBackend(":memory:")
    backend.initialize()
    svc = build_services(backend)
    # Create a test principal
    svc.principals.create_principal(
        kind="user", display_name="Alice", principal_id="alice"
    )
    return svc


class TestInstrumentNoOp:
    """When opentelemetry is not installed, instrument() is a silent no-op."""

    def test_import_succeeds(self):
        from scoped.contrib.otel import instrument

        assert callable(instrument)

    def test_returns_services_unchanged(self, services):
        from scoped.contrib.otel import instrument

        result = instrument(services)
        assert result is services


class TestInstrumentWithOTel:
    """When opentelemetry is available, instrument() wraps methods."""

    def _skip_if_no_otel(self):
        try:
            import opentelemetry  # noqa: F401
        except ImportError:
            pytest.skip("opentelemetry-api not installed")

    def test_wraps_manager_create(self, services):
        self._skip_if_no_otel()
        from scoped.contrib.otel import instrument

        original_create = services.manager.create
        instrument(services)
        assert services.manager.create is not original_create

    def test_create_still_works(self, services):
        self._skip_if_no_otel()
        from scoped.contrib.otel import instrument

        instrument(services)
        obj, ver = services.manager.create(
            object_type="Document",
            owner_id="alice",
            data={"title": "Test"},
        )
        assert obj.object_type == "Document"
        assert ver.version == 1

    def test_get_still_works(self, services):
        self._skip_if_no_otel()
        from scoped.contrib.otel import instrument

        instrument(services)
        obj, _ = services.manager.create(
            object_type="Note",
            owner_id="alice",
            data={"text": "hello"},
        )
        result = services.manager.get(obj.id, principal_id="alice")
        assert result is not None
        assert result.id == obj.id

    def test_audit_record_still_works(self, services):
        self._skip_if_no_otel()
        from scoped.contrib.otel import instrument
        from scoped.types import ActionType

        instrument(services)
        entry = services.audit.record(
            actor_id="alice",
            action=ActionType.CREATE,
            target_type="test",
            target_id="test-1",
        )
        assert entry.actor_id == "alice"

    def test_wraps_secrets(self, services):
        self._skip_if_no_otel()
        from scoped.contrib.otel import instrument

        original_create = services.secrets.create_secret
        instrument(services)
        assert services.secrets.create_secret is not original_create
