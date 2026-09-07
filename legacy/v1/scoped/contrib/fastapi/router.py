"""Pre-built admin/audit API routes for Scoped."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from scoped.contrib.fastapi.dependencies import get_services
from scoped.contrib.fastapi.schemas import (
    HealthCheckSchema,
    HealthStatusSchema,
    TraceEntrySchema,
)

router = APIRouter(prefix="/scoped", tags=["scoped-admin"])


@router.get("/health", response_model=HealthStatusSchema)
def health_check(services=Depends(get_services)):
    """Run Scoped framework health checks."""
    checker = services["health"]
    status = checker.check_all()
    return HealthStatusSchema(
        healthy=status.healthy,
        checks={
            name: HealthCheckSchema(name=name, passed=c.passed, detail=c.detail)
            for name, c in status.checks.items()
        },
    )


@router.get("/audit", response_model=list[TraceEntrySchema])
def list_audit(
    actor_id: str | None = None,
    target_id: str | None = None,
    limit: int = 50,
    services=Depends(get_services),
):
    """Query the Scoped audit trail."""
    query = services["audit_query"]
    kwargs: dict = {"limit": limit}
    if actor_id:
        kwargs["actor_id"] = actor_id
    if target_id:
        kwargs["target_id"] = target_id

    entries = query.query(**kwargs)
    return [TraceEntrySchema.from_entry(e) for e in entries]
