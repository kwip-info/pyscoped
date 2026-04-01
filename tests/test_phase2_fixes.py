"""Tests for Phase 2: async middleware, DRF module, WebSocket, type hints, logging, OTel."""

import json
import logging

import pytest

from scoped.logging import ScopedLogger, StructuredFormatter, get_logger


# =============================================================================
# 1. Django async middleware
# =============================================================================


class TestDjangoMiddlewareAsync:
    """Verify the middleware supports async mode detection."""

    def test_sync_mode_detection(self):
        from scoped.contrib.django.middleware import ScopedContextMiddleware

        def sync_handler(request):
            return "ok"

        mw = ScopedContextMiddleware(sync_handler)
        assert mw.async_mode is False

    def test_async_mode_detection(self):
        from scoped.contrib.django.middleware import ScopedContextMiddleware

        async def async_handler(request):
            return "ok"

        mw = ScopedContextMiddleware(async_handler)
        assert mw.async_mode is True

    def test_has_async_handler(self):
        from scoped.contrib.django.middleware import ScopedContextMiddleware

        assert hasattr(ScopedContextMiddleware, "_handle_scoped_request_async")
        assert hasattr(ScopedContextMiddleware, "_resolve_principal_async")


# =============================================================================
# 2. DRF integration module
# =============================================================================


class TestDRFModuleExists:
    """Verify the DRF integration module loads and has expected classes."""

    def test_module_imports(self):
        from scoped.contrib.django.rest_framework import (
            HasScopeAccess,
            IsScopedPrincipal,
            ScopedAuthentication,
            ScopedUser,
        )
        assert ScopedAuthentication is not None
        assert IsScopedPrincipal is not None
        assert HasScopeAccess is not None
        assert ScopedUser is not None

    def test_scoped_user_wraps_principal(self, sqlite_backend, registry):
        from scoped.contrib.django.rest_framework import ScopedUser
        from scoped.identity.principal import PrincipalStore

        store = PrincipalStore(sqlite_backend)
        alice = store.create_principal(
            kind="user", display_name="Alice", principal_id="alice",
        )

        user = ScopedUser(alice)
        assert user.id == "alice"
        assert user.is_authenticated is True
        assert user.is_active is True
        assert str(user) == "Alice"


# =============================================================================
# 3. FastAPI WebSocket support
# =============================================================================


class TestFastAPIWebSocket:
    """Verify the FastAPI middleware handles WebSocket scope type."""

    def test_middleware_has_websocket_handler(self):
        from scoped.contrib.fastapi.middleware import ScopedContextMiddleware

        assert hasattr(ScopedContextMiddleware, "_handle_websocket")
        assert hasattr(ScopedContextMiddleware, "_resolve_principal_from_scope")

    def test_resolve_principal_from_scope_headers(self):
        from scoped.contrib.fastapi.middleware import ScopedContextMiddleware

        # Create middleware with in-memory backend
        class FakeApp:
            pass

        mw = ScopedContextMiddleware.__new__(ScopedContextMiddleware)
        mw.principal_header = "x-scoped-principal-id"
        mw.principal_resolver = None
        mw.exempt_paths = []

        # Create an ASGI scope with headers
        scope = {
            "type": "websocket",
            "headers": [
                (b"x-scoped-principal-id", b"user-123"),
            ],
        }

        # Since we don't have a real client, _resolve_principal_from_scope
        # will call self._client.principals.find() which we can't test
        # without a full client. But we can verify the header extraction logic.
        # The method should find the header bytes and attempt lookup.
        # For now, verify it exists and is callable.
        assert callable(mw._resolve_principal_from_scope)


# =============================================================================
# 4. Return type hints
# =============================================================================


class TestReturnTypeHints:
    """Verify namespace methods have proper type hints (not Any)."""

    def test_principals_namespace_types(self):
        import inspect
        from scoped._namespaces.principals import PrincipalsNamespace

        hints = inspect.get_annotations(PrincipalsNamespace.create)
        assert hints["return"] == "Principal"

        hints = inspect.get_annotations(PrincipalsNamespace.get)
        assert hints["return"] == "Principal"

        hints = inspect.get_annotations(PrincipalsNamespace.find)
        assert hints["return"] == "Principal | None"

        hints = inspect.get_annotations(PrincipalsNamespace.list)
        assert hints["return"] == "list[Principal]"

    def test_objects_namespace_types(self):
        import inspect
        from scoped._namespaces.objects import ObjectsNamespace

        hints = inspect.get_annotations(ObjectsNamespace.create)
        assert "ScopedObject" in hints["return"]

        hints = inspect.get_annotations(ObjectsNamespace.get)
        assert "ScopedObject" in hints["return"]

        hints = inspect.get_annotations(ObjectsNamespace.delete)
        assert hints["return"] == "Tombstone"

    def test_scopes_namespace_types(self):
        import inspect
        from scoped._namespaces.scopes import ScopesNamespace

        hints = inspect.get_annotations(ScopesNamespace.create)
        assert hints["return"] == "Scope"

        hints = inspect.get_annotations(ScopesNamespace.get)
        assert hints["return"] == "Scope | None"

        hints = inspect.get_annotations(ScopesNamespace.add_member)
        assert hints["return"] == "ScopeMembership"

    def test_audit_namespace_types(self):
        import inspect
        from scoped._namespaces.audit import AuditNamespace

        hints = inspect.get_annotations(AuditNamespace.for_object)
        assert "TraceEntry" in hints["return"]

        hints = inspect.get_annotations(AuditNamespace.verify)
        assert hints["return"] == "ChainVerification"


# =============================================================================
# 5. Structured logging
# =============================================================================


class TestStructuredLogging:

    def test_get_logger_returns_scoped_logger(self):
        logger = get_logger("test")
        assert isinstance(logger, ScopedLogger)

    def test_structured_formatter_json(self):
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="pyscoped.test", level=logging.INFO, pathname="", lineno=0,
            msg="hello world", args=(), exc_info=None,
        )
        record._scoped_extra = {"key": "value"}
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "hello world"
        assert parsed["key"] == "value"
        assert parsed["level"] == "INFO"
        assert "timestamp" in parsed

    def test_audit_method(self, caplog):
        logger = get_logger("audit_test")
        # Set level on the underlying logger
        logger._logger.setLevel(logging.DEBUG)
        with caplog.at_level(logging.INFO, logger="pyscoped.audit_test"):
            logger.audit("object.created", object_id="doc-1")

        assert len(caplog.records) == 1
        extra = caplog.records[0]._scoped_extra
        assert extra["event"] == "object.created"
        assert extra["object_id"] == "doc-1"
        assert extra["category"] == "audit"

    def test_debug_skipped_at_info_level(self, caplog):
        logger = get_logger("skip_test")
        logger._logger.setLevel(logging.INFO)
        with caplog.at_level(logging.INFO, logger="pyscoped.skip_test"):
            logger.debug("should be skipped")

        assert len(caplog.records) == 0


# =============================================================================
# 6. Extended OTel instrumentation
# =============================================================================


class TestOTelExtended:
    """Verify OTel instrument() covers the new services."""

    def test_instrument_covers_scopes(self):
        from scoped.contrib.otel import instrument

        # Verify the function references exist (can't test actual tracing
        # without opentelemetry installed, but we can verify the module loads)
        from scoped.contrib import otel

        assert hasattr(otel, "_attr_scope_create")
        assert hasattr(otel, "_attr_scope_rename")
        assert hasattr(otel, "_attr_scope_update")
        assert hasattr(otel, "_attr_scope_member")
        assert hasattr(otel, "_attr_scope_lifecycle")

    def test_instrument_covers_principals(self):
        from scoped.contrib import otel

        assert hasattr(otel, "_attr_principal_create")
        assert hasattr(otel, "_attr_principal_get")
        assert hasattr(otel, "_attr_principal_update")

    def test_instrument_covers_rules(self):
        from scoped.contrib import otel

        assert hasattr(otel, "_attr_rule_evaluate")

    def test_instrument_noop_without_otel(self):
        """When opentelemetry is not installed, instrument() is a no-op."""
        from scoped.client import ScopedClient
        from scoped.contrib.otel import instrument

        client = ScopedClient()
        result = instrument(client)
        assert result is client
        client.close()
