"""Admin blueprint for Flask — health and audit endpoints."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

admin_bp = Blueprint("scoped_admin", __name__, url_prefix="/scoped")


@admin_bp.route("/health")
def health():
    """Run Scoped framework health checks."""
    ext = current_app.extensions["scoped"]
    checker = ext.services["health"]
    status = checker.check_all()
    return jsonify(
        {
            "healthy": status.healthy,
            "checks": {
                name: {"passed": c.passed, "detail": c.detail}
                for name, c in status.checks.items()
            },
        }
    )


@admin_bp.route("/audit")
def audit():
    """Query the Scoped audit trail."""
    ext = current_app.extensions["scoped"]
    query = ext.services["audit_query"]

    kwargs: dict = {"limit": int(request.args.get("limit", 50))}
    actor_id = request.args.get("actor_id")
    if actor_id:
        kwargs["actor_id"] = actor_id
    target_id = request.args.get("target_id")
    if target_id:
        kwargs["target_id"] = target_id

    entries = query.query(**kwargs)
    return jsonify(
        [
            {
                "id": e.id,
                "sequence": e.sequence,
                "actor_id": e.actor_id,
                "action": e.action.value if hasattr(e.action, "value") else str(e.action),
                "target_type": e.target_type,
                "target_id": e.target_id,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in entries
        ]
    )
