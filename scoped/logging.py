"""Structured logging for pyscoped.

Provides a JSON-structured logger that enriches log records with
pyscoped context (principal ID, scope ID, action) for correlation
in log aggregation systems.

Usage::

    from scoped.logging import get_logger

    logger = get_logger(__name__)
    logger.audit("object.created", object_id="doc-1", object_type="invoice")
    logger.info("Processing complete", count=5)

The logger respects standard Python logging levels and configuration.
Set ``SCOPED_LOG_LEVEL`` environment variable or configure the
``"pyscoped"`` logger via ``logging.config``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any


_DEFAULT_LEVEL = os.environ.get("SCOPED_LOG_LEVEL", "INFO").upper()


class StructuredFormatter(logging.Formatter):
    """Format log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge extra structured fields
        extra = getattr(record, "_scoped_extra", None)
        if extra:
            payload.update(extra)

        # Add context from ScopedContext if available
        try:
            from scoped.identity.context import ScopedContext

            ctx = ScopedContext.current_or_none()
            if ctx is not None:
                payload["principal_id"] = ctx.principal_id
        except Exception:
            pass

        if record.exc_info and record.exc_info[1]:
            payload["exception"] = str(record.exc_info[1])
            payload["exception_type"] = type(record.exc_info[1]).__name__

        return json.dumps(payload, default=str)


class ScopedLogger:
    """Wrapper around ``logging.Logger`` with structured field support.

    All standard log methods (``info``, ``warning``, ``error``, ``debug``)
    accept keyword arguments that are included as structured fields in
    the JSON output.

    The ``audit()`` method logs at INFO level with ``event_type`` set,
    designed for pyscoped operation logging.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def audit(self, event: str, **fields: Any) -> None:
        """Log a pyscoped audit-level event (INFO)."""
        fields["event"] = event
        fields["category"] = "audit"
        self._log(logging.INFO, event, fields)

    def debug(self, msg: str, **fields: Any) -> None:
        self._log(logging.DEBUG, msg, fields)

    def info(self, msg: str, **fields: Any) -> None:
        self._log(logging.INFO, msg, fields)

    def warning(self, msg: str, **fields: Any) -> None:
        self._log(logging.WARNING, msg, fields)

    def error(self, msg: str, **fields: Any) -> None:
        self._log(logging.ERROR, msg, fields)

    def exception(self, msg: str, **fields: Any) -> None:
        self._log(logging.ERROR, msg, fields, exc_info=True)

    def _log(
        self,
        level: int,
        msg: str,
        fields: dict[str, Any],
        exc_info: bool = False,
    ) -> None:
        if not self._logger.isEnabledFor(level):
            return
        record = self._logger.makeRecord(
            self._logger.name, level, "(scoped)", 0, msg, (), None,
        )
        record._scoped_extra = fields  # type: ignore[attr-defined]
        if exc_info:
            import sys

            record.exc_info = sys.exc_info()
        self._logger.handle(record)


def get_logger(name: str | None = None) -> ScopedLogger:
    """Return a structured ``ScopedLogger``.

    If the ``"pyscoped"`` logger has no handlers, a default
    ``StreamHandler`` with ``StructuredFormatter`` is attached.

    Args:
        name: Logger name. Defaults to ``"pyscoped"``.

    Returns:
        A ``ScopedLogger`` instance.
    """
    logger_name = f"pyscoped.{name}" if name else "pyscoped"
    logger = logging.getLogger(logger_name)

    # Ensure the root pyscoped logger has at least one handler
    root = logging.getLogger("pyscoped")
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredFormatter())
        root.addHandler(handler)
        root.setLevel(getattr(logging, _DEFAULT_LEVEL, logging.INFO))

    return ScopedLogger(logger)
