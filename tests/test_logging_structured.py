"""Tests for 8B: Structured logging integration in key modules."""

import json
import logging

import pytest

from scoped.logging import ScopedLogger, StructuredFormatter, get_logger


class TestGetLoggerIntegration:
    """Verify get_logger is wired into the 6 key modules."""

    def test_objects_manager_logger(self):
        from scoped.objects.manager import _logger
        assert isinstance(_logger, ScopedLogger)

    def test_audit_writer_logger(self):
        from scoped.audit.writer import _logger
        assert isinstance(_logger, ScopedLogger)

    def test_rules_engine_logger(self):
        from scoped.rules.engine import _logger
        assert isinstance(_logger, ScopedLogger)

    def test_secrets_vault_logger(self):
        from scoped.secrets.vault import _logger
        assert isinstance(_logger, ScopedLogger)

    def test_tenancy_lifecycle_logger(self):
        from scoped.tenancy.lifecycle import _logger
        assert isinstance(_logger, ScopedLogger)

    def test_sync_transport_logger(self):
        from scoped.sync.transport import _logger
        assert isinstance(_logger, ScopedLogger)


class TestStructuredFormatter:
    """Verify JSON output format."""

    def test_json_output(self):
        fmt = StructuredFormatter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "hello world", (), None,
        )
        output = fmt.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "hello world"
        assert parsed["level"] == "INFO"
        assert "timestamp" in parsed

    def test_extra_fields_included(self):
        fmt = StructuredFormatter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "event", (), None,
        )
        record._scoped_extra = {"object_id": "abc", "action": "create"}  # type: ignore
        output = fmt.format(record)
        parsed = json.loads(output)
        assert parsed["object_id"] == "abc"
        assert parsed["action"] == "create"


class TestScopedLogger:
    """Verify ScopedLogger methods."""

    def test_info_with_fields(self, caplog):
        logger = get_logger("test.info")
        with caplog.at_level(logging.INFO, logger="pyscoped.test.info"):
            logger.info("hello", key="value")
        # Structured formatter handles the output; caplog sees the raw message
        assert any("hello" in r.message for r in caplog.records)

    def test_debug_below_level_skipped(self, caplog):
        logger = get_logger("test.debug")
        # Default level is INFO, DEBUG should be skipped
        with caplog.at_level(logging.WARNING, logger="pyscoped.test.debug"):
            logger.debug("should not appear")
        assert not any("should not appear" in r.message for r in caplog.records)

    def test_audit_method(self, caplog):
        logger = get_logger("test.audit")
        with caplog.at_level(logging.INFO, logger="pyscoped.test.audit"):
            logger.audit("object.created", object_id="d1")
        assert any("object.created" in r.message for r in caplog.records)
