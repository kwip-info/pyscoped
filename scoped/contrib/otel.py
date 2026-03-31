"""OpenTelemetry instrumentation for pyscoped.

Wraps key service methods with OTel spans to provide distributed tracing
and latency visibility for production deployments.

Usage::

    import scoped
    from scoped.contrib.otel import instrument

    client = scoped.init(database_url="postgresql://...")
    instrument(client)  # Now all operations emit OTel spans

Also accepts raw ``ScopedServices`` for advanced use::

    from scoped.manifest._services import build_services
    from scoped.contrib.otel import instrument

    services = build_services(backend)
    instrument(services)

Requires ``opentelemetry-api`` — install via ``pip install pyscoped[otel]``.
If the package is not installed, ``instrument()`` is a silent no-op.
"""

from __future__ import annotations

import functools
from typing import Any

try:
    from opentelemetry import trace

    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False


def instrument(target: Any) -> Any:
    """Wrap key service methods with OpenTelemetry spans.

    Accepts either a ``ScopedClient`` or a ``ScopedServices`` instance.
    Mutates *target* in-place and returns it for convenience. If
    ``opentelemetry-api`` is not installed the call is a silent no-op.

    Instrumented operations:

    - Object CRUD — create, get, update, tombstone, list_objects
    - Audit recording — record
    - Secrets — create_secret, rotate, resolve

    Args:
        target: A ``ScopedClient`` or ``ScopedServices`` instance.

    Returns:
        The same *target*, with methods wrapped.

    Example::

        import scoped
        from scoped.contrib.otel import instrument

        client = scoped.init(database_url="postgresql://...")
        instrument(client)
    """
    if not _HAS_OTEL:
        return target

    # Accept either ScopedClient (has .services) or ScopedServices directly
    services = getattr(target, "services", target)

    tracer = trace.get_tracer("pyscoped", tracer_provider=trace.get_tracer_provider())

    # -- Object manager --------------------------------------------------------
    mgr = services.manager
    _wrap(mgr, "create", tracer, "scoped.object.create", _attr_create)
    _wrap(mgr, "get", tracer, "scoped.object.get", _attr_get)
    _wrap(mgr, "update", tracer, "scoped.object.update", _attr_update)
    _wrap(mgr, "tombstone", tracer, "scoped.object.tombstone", _attr_tombstone)
    _wrap(mgr, "list_objects", tracer, "scoped.object.list", _attr_list)

    # -- Audit writer ----------------------------------------------------------
    _wrap(services.audit, "record", tracer, "scoped.audit.record", _attr_audit)

    # -- Secrets vault ---------------------------------------------------------
    vault = services.secrets
    _wrap(vault, "create_secret", tracer, "scoped.secret.create", _attr_secret_create)
    _wrap(vault, "rotate", tracer, "scoped.secret.rotate", _attr_secret_rotate)
    _wrap(vault, "resolve", tracer, "scoped.secret.resolve", _attr_secret_resolve)

    return target


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _wrap(
    obj: Any,
    method_name: str,
    tracer: Any,
    span_name: str,
    attr_fn: Any,
) -> None:
    """Replace *obj.method_name* with a version that creates an OTel span."""
    original = getattr(obj, method_name)

    @functools.wraps(original)
    def _traced(*args: Any, **kwargs: Any) -> Any:
        with tracer.start_as_current_span(span_name) as span:
            # Set input attributes
            try:
                attr_fn(span, kwargs)
            except Exception:
                pass

            try:
                result = original(*args, **kwargs)
            except Exception as exc:
                span.set_status(trace.StatusCode.ERROR, str(exc))
                span.record_exception(exc)
                raise

            # Set result attributes
            try:
                _set_result_attrs(span, span_name, result)
            except Exception:
                pass

            span.set_status(trace.StatusCode.OK)
            return result

    setattr(obj, method_name, _traced)


def _set_result_attrs(span: Any, span_name: str, result: Any) -> None:
    """Set span attributes based on the return value."""
    if span_name == "scoped.object.get":
        span.set_attribute("scoped.found", result is not None)
    elif span_name == "scoped.object.create" and isinstance(result, tuple):
        obj, ver = result
        span.set_attribute("scoped.object_id", obj.id)
        span.set_attribute("scoped.version", ver.version)
    elif span_name == "scoped.object.update" and isinstance(result, tuple):
        _, ver = result
        span.set_attribute("scoped.version", ver.version)
    elif span_name == "scoped.object.list" and isinstance(result, list):
        span.set_attribute("scoped.count", len(result))
    elif span_name == "scoped.secret.create" and isinstance(result, tuple):
        secret, _ = result
        span.set_attribute("scoped.secret_id", secret.id)
    elif span_name == "scoped.secret.resolve":
        span.set_attribute("scoped.success", True)


# ---------------------------------------------------------------------------
# Attribute extractors — pull span attributes from kwargs
# ---------------------------------------------------------------------------

def _attr_create(span: Any, kwargs: dict) -> None:
    if "object_type" in kwargs:
        span.set_attribute("scoped.object_type", kwargs["object_type"])
    if "owner_id" in kwargs:
        span.set_attribute("scoped.owner_id", kwargs["owner_id"])


def _attr_get(span: Any, kwargs: dict) -> None:
    if "principal_id" in kwargs:
        span.set_attribute("scoped.principal_id", kwargs["principal_id"])


def _attr_update(span: Any, kwargs: dict) -> None:
    if "principal_id" in kwargs:
        span.set_attribute("scoped.principal_id", kwargs["principal_id"])


def _attr_tombstone(span: Any, kwargs: dict) -> None:
    if "principal_id" in kwargs:
        span.set_attribute("scoped.principal_id", kwargs["principal_id"])
    if "reason" in kwargs:
        span.set_attribute("scoped.reason", kwargs["reason"])


def _attr_list(span: Any, kwargs: dict) -> None:
    if "principal_id" in kwargs:
        span.set_attribute("scoped.principal_id", kwargs["principal_id"])
    if "object_type" in kwargs:
        span.set_attribute("scoped.object_type", kwargs["object_type"])
    if "limit" in kwargs:
        span.set_attribute("scoped.limit", kwargs["limit"])


def _attr_audit(span: Any, kwargs: dict) -> None:
    if "action" in kwargs:
        span.set_attribute("scoped.action", str(kwargs["action"]))
    if "target_type" in kwargs:
        span.set_attribute("scoped.target_type", kwargs["target_type"])
    if "target_id" in kwargs:
        span.set_attribute("scoped.target_id", kwargs["target_id"])


def _attr_secret_create(span: Any, kwargs: dict) -> None:
    if "classification" in kwargs:
        span.set_attribute("scoped.classification", kwargs["classification"])


def _attr_secret_rotate(span: Any, kwargs: dict) -> None:
    if "reason" in kwargs:
        span.set_attribute("scoped.reason", kwargs["reason"])


def _attr_secret_resolve(span: Any, kwargs: dict) -> None:
    if "accessor_id" in kwargs:
        span.set_attribute("scoped.accessor_id", kwargs["accessor_id"])
