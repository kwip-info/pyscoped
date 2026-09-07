"""Built-in rule factories for common patterns."""

from __future__ import annotations

from typing import Any

from scoped.rules.engine import RuleStore
from scoped.rules.models import (
    BindingTargetType,
    Rule,
    RuleEffect,
    RuleType,
)


def allow_crud_in_scope(
    store: RuleStore,
    *,
    scope_id: str,
    created_by: str,
    object_type: str | None = None,
    actions: list[str] | None = None,
    name: str = "Allow CRUD in scope",
    priority: int = 0,
) -> Rule:
    """Create an ALLOW access rule bound to a scope for CRUD operations."""
    conditions: dict[str, Any] = {}
    if actions:
        conditions["action"] = actions
    else:
        conditions["action"] = ["create", "read", "update", "delete"]
    if object_type:
        conditions["object_type"] = object_type

    rule = store.create_rule(
        name=name,
        rule_type=RuleType.ACCESS,
        effect=RuleEffect.ALLOW,
        conditions=conditions,
        priority=priority,
        created_by=created_by,
    )
    store.bind_rule(
        rule.id,
        target_type=BindingTargetType.SCOPE,
        target_id=scope_id,
        bound_by=created_by,
    )
    return rule


def deny_action_in_scope(
    store: RuleStore,
    *,
    scope_id: str,
    action: str,
    created_by: str,
    object_type: str | None = None,
    name: str = "Deny action in scope",
    priority: int = 10,
) -> Rule:
    """Create a DENY rule for a specific action within a scope."""
    conditions: dict[str, Any] = {"action": action}
    if object_type:
        conditions["object_type"] = object_type

    rule = store.create_rule(
        name=name,
        rule_type=RuleType.ACCESS,
        effect=RuleEffect.DENY,
        conditions=conditions,
        priority=priority,
        created_by=created_by,
    )
    store.bind_rule(
        rule.id,
        target_type=BindingTargetType.SCOPE,
        target_id=scope_id,
        bound_by=created_by,
    )
    return rule


def deny_sharing_outside_scope(
    store: RuleStore,
    *,
    scope_id: str,
    created_by: str,
    name: str = "Deny sharing outside scope",
    priority: int = 10,
) -> Rule:
    """Create a DENY sharing rule preventing projection outside a scope boundary."""
    conditions: dict[str, Any] = {"action": "projection"}

    rule = store.create_rule(
        name=name,
        rule_type=RuleType.SHARING,
        effect=RuleEffect.DENY,
        conditions=conditions,
        priority=priority,
        created_by=created_by,
    )
    store.bind_rule(
        rule.id,
        target_type=BindingTargetType.SCOPE,
        target_id=scope_id,
        bound_by=created_by,
    )
    return rule


def restrict_to_principal_kind(
    store: RuleStore,
    *,
    principal_kind: str,
    action: str,
    scope_id: str,
    created_by: str,
    name: str = "Restrict by principal kind",
    priority: int = 5,
) -> Rule:
    """Create a DENY rule that blocks a specific action for a principal kind."""
    conditions: dict[str, Any] = {
        "action": action,
        "principal_kind": principal_kind,
    }

    rule = store.create_rule(
        name=name,
        rule_type=RuleType.CONSTRAINT,
        effect=RuleEffect.DENY,
        conditions=conditions,
        priority=priority,
        created_by=created_by,
    )
    store.bind_rule(
        rule.id,
        target_type=BindingTargetType.SCOPE,
        target_id=scope_id,
        bound_by=created_by,
    )
    return rule


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

def redact_fields_for_kind(
    store: RuleStore,
    *,
    object_type: str,
    principal_kind: str,
    redactions: dict[str, dict[str, Any]],
    scope_id: str,
    created_by: str,
    name: str = "Redact fields by principal kind",
    priority: int = 0,
) -> Rule:
    """Create a REDACTION rule that masks fields for a given viewer kind.

    *redactions* maps field names to strategy configs::

        {"card_number": {"strategy": "mask", "visible_chars": 4}}
    """
    conditions: dict[str, Any] = {
        "object_type": object_type,
        "principal_kind": principal_kind,
        "redactions": redactions,
    }

    rule = store.create_rule(
        name=name,
        rule_type=RuleType.REDACTION,
        effect=RuleEffect.DENY,
        conditions=conditions,
        priority=priority,
        created_by=created_by,
    )
    store.bind_rule(
        rule.id,
        target_type=BindingTargetType.SCOPE,
        target_id=scope_id,
        bound_by=created_by,
    )
    return rule


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def rate_limit_action(
    store: RuleStore,
    *,
    actions: list[str],
    max_count: int,
    window_seconds: int,
    scope_id: str,
    created_by: str,
    name: str = "Rate limit",
    priority: int = 0,
) -> Rule:
    """Create a RATE_LIMIT rule for specific actions."""
    conditions: dict[str, Any] = {
        "action": actions,
        "rate_limit": {
            "max_count": max_count,
            "window_seconds": window_seconds,
        },
    }

    rule = store.create_rule(
        name=name,
        rule_type=RuleType.RATE_LIMIT,
        effect=RuleEffect.DENY,
        conditions=conditions,
        priority=priority,
        created_by=created_by,
    )
    store.bind_rule(
        rule.id,
        target_type=BindingTargetType.SCOPE,
        target_id=scope_id,
        bound_by=created_by,
    )
    return rule


# ---------------------------------------------------------------------------
# Quotas
# ---------------------------------------------------------------------------

def quota_for_object_type(
    store: RuleStore,
    *,
    object_type: str,
    max_count: int,
    scope_id: str,
    created_by: str,
    name: str = "Quota",
    priority: int = 0,
) -> Rule:
    """Create a QUOTA rule limiting how many objects of a type can exist."""
    conditions: dict[str, Any] = {
        "object_type": object_type,
        "quota": {
            "max_count": max_count,
        },
    }

    rule = store.create_rule(
        name=name,
        rule_type=RuleType.QUOTA,
        effect=RuleEffect.DENY,
        conditions=conditions,
        priority=priority,
        created_by=created_by,
    )
    store.bind_rule(
        rule.id,
        target_type=BindingTargetType.SCOPE,
        target_id=scope_id,
        bound_by=created_by,
    )
    return rule


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

def feature_flag(
    store: RuleStore,
    *,
    feature_name: str,
    enabled: bool = True,
    rollout_percentage: int = 100,
    scope_id: str | None = None,
    created_by: str,
    name: str | None = None,
    priority: int = 0,
) -> Rule:
    """Create a FEATURE_FLAG rule."""
    conditions: dict[str, Any] = {
        "feature_flag": {
            "feature_name": feature_name,
            "enabled": enabled,
            "rollout_percentage": rollout_percentage,
        },
    }
    if scope_id:
        conditions["scope_id"] = scope_id

    rule = store.create_rule(
        name=name or f"Feature flag: {feature_name}",
        rule_type=RuleType.FEATURE_FLAG,
        effect=RuleEffect.ALLOW if enabled else RuleEffect.DENY,
        conditions=conditions,
        priority=priority,
        created_by=created_by,
    )

    if scope_id:
        store.bind_rule(
            rule.id,
            target_type=BindingTargetType.SCOPE,
            target_id=scope_id,
            bound_by=created_by,
        )
    return rule
