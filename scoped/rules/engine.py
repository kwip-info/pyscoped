"""Rule evaluation engine — deny-overrides model.

Evaluates rules for (principal, action, target, scope) tuples.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from scoped.rules.compiler import CompiledRuleSet, RuleCompiler
from scoped.rules.models import (
    BindingTargetType,
    EvaluationResult,
    Rule,
    RuleBinding,
    RuleEffect,
    RuleType,
    RuleVersion,
    binding_from_row,
    rule_from_row,
    rule_version_from_row,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import rule_bindings, rule_versions, rules
from scoped.ids import BindingId, RuleId, VersionId
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc


class RuleStore:
    """CRUD operations for rules, versions, and bindings."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._audit = audit_writer

    # ------------------------------------------------------------------
    # Rule CRUD
    # ------------------------------------------------------------------

    def create_rule(
        self,
        *,
        name: str,
        rule_type: RuleType,
        effect: RuleEffect,
        conditions: dict[str, Any] | Any | None = None,
        priority: int = 0,
        description: str = "",
        created_by: str,
    ) -> Rule:
        """Create a new rule with its first version.

        ``conditions`` can be a raw dict or a typed ``RuleConditions``
        model (e.g. ``AccessCondition``, ``QuotaCondition``).  Typed
        models are validated immediately and serialized to dict for storage.
        """
        from scoped.rules.conditions import conditions_to_dict

        ts = now_utc()
        rule_id = RuleId.generate()
        conds = conditions_to_dict(conditions) if conditions else {}

        rule = Rule(
            id=rule_id,
            name=name,
            description=description,
            rule_type=rule_type,
            effect=effect,
            priority=priority,
            conditions=conds,
            created_at=ts,
            created_by=created_by,
        )

        stmt = sa.insert(rules).values(
            id=rule.id, name=rule.name, description=rule.description,
            rule_type=rule.rule_type.value, effect=rule.effect.value,
            priority=rule.priority, conditions_json=json.dumps(conds),
            registry_entry_id=rule.registry_entry_id,
            created_at=rule.created_at.isoformat(),
            created_by=rule.created_by, lifecycle=rule.lifecycle.name,
            current_version=rule.current_version,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        # Create version 1
        self._create_version(rule, change_reason="created")

        if self._audit:
            self._audit.record(
                actor_id=created_by,
                action=ActionType.RULE_CHANGE,
                target_type="Rule",
                target_id=rule_id,
                after_state=rule.snapshot(),
            )

        return rule

    def get_rule(self, rule_id: str) -> Rule | None:
        stmt = sa.select(rules).where(rules.c.id == rule_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            return None
        return rule_from_row(row)

    def list_rules(
        self,
        *,
        rule_type: RuleType | None = None,
        effect: RuleEffect | None = None,
        active_only: bool = True,
    ) -> list[Rule]:
        stmt = sa.select(rules)
        if rule_type is not None:
            stmt = stmt.where(rules.c.rule_type == rule_type.value)
        if effect is not None:
            stmt = stmt.where(rules.c.effect == effect.value)
        if active_only:
            stmt = stmt.where(rules.c.lifecycle == Lifecycle.ACTIVE.name)
        stmt = stmt.order_by(rules.c.priority.desc())
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [rule_from_row(r) for r in rows]

    def update_rule(
        self,
        rule_id: str,
        *,
        updated_by: str,
        conditions: dict[str, Any] | None = None,
        effect: RuleEffect | None = None,
        priority: int | None = None,
        change_reason: str = "",
    ) -> Rule:
        """Update a rule, creating a new version. Never modifies existing versions."""
        rule = self.get_rule(rule_id)
        if rule is None:
            raise ValueError(f"Rule {rule_id} not found")

        before = rule.snapshot()

        if conditions is not None:
            rule.conditions = conditions
        if effect is not None:
            rule.effect = effect
        if priority is not None:
            rule.priority = priority

        rule.current_version += 1

        stmt = sa.update(rules).where(rules.c.id == rule_id).values(
            conditions_json=json.dumps(rule.conditions),
            effect=rule.effect.value,
            priority=rule.priority,
            current_version=rule.current_version,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        self._create_version(rule, change_reason=change_reason, created_by=updated_by)

        if self._audit:
            self._audit.record(
                actor_id=updated_by,
                action=ActionType.RULE_CHANGE,
                target_type="Rule",
                target_id=rule_id,
                before_state=before,
                after_state=rule.snapshot(),
            )

        return rule

    def archive_rule(self, rule_id: str, *, archived_by: str) -> Rule:
        """Archive a rule (soft delete). Also archives its bindings."""
        rule = self.get_rule(rule_id)
        if rule is None:
            raise ValueError(f"Rule {rule_id} not found")

        stmt1 = sa.update(rules).where(rules.c.id == rule_id).values(
            lifecycle=Lifecycle.ARCHIVED.name,
        )
        sql, params = compile_for(stmt1, self._backend.dialect)
        self._backend.execute(sql, params)

        stmt2 = sa.update(rule_bindings).where(
            rule_bindings.c.rule_id == rule_id,
            rule_bindings.c.lifecycle == Lifecycle.ACTIVE.name,
        ).values(lifecycle=Lifecycle.ARCHIVED.name)
        sql, params = compile_for(stmt2, self._backend.dialect)
        self._backend.execute(sql, params)

        rule.lifecycle = Lifecycle.ARCHIVED

        if self._audit:
            self._audit.record(
                actor_id=archived_by,
                action=ActionType.LIFECYCLE_CHANGE,
                target_type="Rule",
                target_id=rule_id,
                after_state={"lifecycle": "ARCHIVED"},
            )

        return rule

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    def get_versions(self, rule_id: str) -> list[RuleVersion]:
        stmt = sa.select(rule_versions).where(
            rule_versions.c.rule_id == rule_id,
        ).order_by(rule_versions.c.version.asc())
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [rule_version_from_row(r) for r in rows]

    def _create_version(
        self,
        rule: Rule,
        *,
        change_reason: str = "",
        created_by: str | None = None,
    ) -> RuleVersion:
        ts = now_utc()
        ver_id = VersionId.generate()
        ver = RuleVersion(
            id=ver_id,
            rule_id=rule.id,
            version=rule.current_version,
            conditions=rule.conditions,
            effect=rule.effect,
            priority=rule.priority,
            created_at=ts,
            created_by=created_by or rule.created_by,
            change_reason=change_reason,
        )
        stmt = sa.insert(rule_versions).values(
            id=ver.id, rule_id=ver.rule_id, version=ver.version,
            conditions_json=json.dumps(ver.conditions),
            effect=ver.effect.value, priority=ver.priority,
            created_at=ver.created_at.isoformat(),
            created_by=ver.created_by, change_reason=ver.change_reason,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
        return ver

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    def bind_rule(
        self,
        rule_id: str,
        *,
        target_type: BindingTargetType,
        target_id: str,
        bound_by: str,
    ) -> RuleBinding:
        """Bind a rule to a target."""
        ts = now_utc()
        binding_id = BindingId.generate()
        binding = RuleBinding(
            id=binding_id,
            rule_id=rule_id,
            target_type=target_type,
            target_id=target_id,
            bound_at=ts,
            bound_by=bound_by,
        )

        stmt = sa.insert(rule_bindings).values(
            id=binding.id, rule_id=binding.rule_id,
            target_type=binding.target_type.value,
            target_id=binding.target_id,
            bound_at=binding.bound_at.isoformat(),
            bound_by=binding.bound_by, lifecycle=binding.lifecycle.name,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        return binding

    def unbind_rule(
        self,
        rule_id: str,
        *,
        target_type: BindingTargetType,
        target_id: str,
    ) -> bool:
        """Remove a rule binding. Returns True if removed."""
        stmt = sa.select(sa.literal(1)).select_from(rule_bindings).where(
            rule_bindings.c.rule_id == rule_id,
            rule_bindings.c.target_type == target_type.value,
            rule_bindings.c.target_id == target_id,
            rule_bindings.c.lifecycle == Lifecycle.ACTIVE.name,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            return False

        stmt2 = sa.update(rule_bindings).where(
            rule_bindings.c.rule_id == rule_id,
            rule_bindings.c.target_type == target_type.value,
            rule_bindings.c.target_id == target_id,
            rule_bindings.c.lifecycle == Lifecycle.ACTIVE.name,
        ).values(lifecycle=Lifecycle.ARCHIVED.name)
        sql, params = compile_for(stmt2, self._backend.dialect)
        self._backend.execute(sql, params)
        return True

    def get_bindings(
        self,
        rule_id: str,
        *,
        active_only: bool = True,
    ) -> list[RuleBinding]:
        stmt = sa.select(rule_bindings).where(rule_bindings.c.rule_id == rule_id)
        if active_only:
            stmt = stmt.where(rule_bindings.c.lifecycle == Lifecycle.ACTIVE.name)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [binding_from_row(r) for r in rows]

    def get_target_bindings(
        self,
        target_type: BindingTargetType,
        target_id: str,
        *,
        active_only: bool = True,
    ) -> list[RuleBinding]:
        """Get all bindings for a specific target."""
        stmt = sa.select(rule_bindings).where(
            rule_bindings.c.target_type == target_type.value,
            rule_bindings.c.target_id == target_id,
        )
        if active_only:
            stmt = stmt.where(rule_bindings.c.lifecycle == Lifecycle.ACTIVE.name)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [binding_from_row(r) for r in rows]


class RuleEngine:
    """Evaluate rules using deny-overrides model.

    1. Collect all matching rules
    2. If ANY DENY rule matches → denied
    3. If at least one ALLOW matches and no DENY → allowed
    4. If no rules match → denied (default-deny)
    """

    def __init__(
        self,
        backend: StorageBackend,
        *,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._compiler = RuleCompiler(backend)
        self._audit = audit_writer

    def evaluate(
        self,
        *,
        action: str,
        principal_id: str | None = None,
        principal_kind: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        scope_id: str | None = None,
    ) -> EvaluationResult:
        """Evaluate rules for an access request.

        Collects all matching rules bound to relevant targets,
        applies deny-overrides, and returns the result.
        """
        # Collect candidate rules from all relevant binding targets
        candidates: list[Rule] = []

        # Rules bound to this scope
        if scope_id:
            candidates.extend(
                self._rules_for_target(BindingTargetType.SCOPE, scope_id)
            )

        # Rules bound to this principal
        if principal_id:
            candidates.extend(
                self._rules_for_target(BindingTargetType.PRINCIPAL, principal_id)
            )

        # Rules bound to this object type
        if object_type:
            candidates.extend(
                self._rules_for_target(BindingTargetType.OBJECT_TYPE, object_type)
            )

        # Rules bound to this specific object
        if object_id:
            candidates.extend(
                self._rules_for_target(BindingTargetType.OBJECT, object_id)
            )

        # Deduplicate by rule ID
        seen: set[str] = set()
        unique: list[Rule] = []
        for rule in candidates:
            if rule.id not in seen:
                seen.add(rule.id)
                unique.append(rule)

        # Filter by condition matching
        matching = [r for r in unique if self._matches_conditions(
            r, action=action, principal_kind=principal_kind,
            object_type=object_type, scope_id=scope_id,
        )]

        # Sort by priority (highest first)
        matching.sort(key=lambda r: r.priority, reverse=True)

        # Apply deny-overrides
        deny_rules = tuple(r for r in matching if r.effect == RuleEffect.DENY)
        allow_rules = tuple(r for r in matching if r.effect == RuleEffect.ALLOW)

        if deny_rules:
            allowed = False
        elif allow_rules:
            allowed = True
        else:
            allowed = False  # default-deny

        result = EvaluationResult(
            allowed=allowed,
            matching_rules=tuple(matching),
            deny_rules=deny_rules,
            allow_rules=allow_rules,
        )

        if self._audit:
            self._audit.record(
                actor_id=principal_id or "system",
                action=ActionType.ACCESS_CHECK,
                target_type=object_type or "unknown",
                target_id=object_id or "unknown",
                scope_id=scope_id,
                metadata={
                    "action_checked": action,
                    "result": "allowed" if allowed else "denied",
                    "rules_matched": len(matching),
                },
            )

        return result

    def _rules_for_target(
        self,
        target_type: BindingTargetType,
        target_id: str,
    ) -> list[Rule]:
        """Load active rules bound to a target."""
        stmt = sa.select(rules).select_from(
            rules.join(rule_bindings, rules.c.id == rule_bindings.c.rule_id)
        ).where(
            rule_bindings.c.target_type == target_type.value,
            rule_bindings.c.target_id == target_id,
            rule_bindings.c.lifecycle == Lifecycle.ACTIVE.name,
            rules.c.lifecycle == Lifecycle.ACTIVE.name,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [rule_from_row(r) for r in rows]

    @staticmethod
    def _matches_conditions(
        rule: Rule,
        *,
        action: str,
        principal_kind: str | None,
        object_type: str | None,
        scope_id: str | None,
    ) -> bool:
        """Check if a rule's conditions match the request context.

        Empty conditions = matches everything (universal rule).
        Each condition key narrows the match.
        """
        conds = rule.conditions
        if not conds:
            return True

        # Check action condition
        if "action" in conds:
            allowed_actions = conds["action"]
            if isinstance(allowed_actions, list):
                if action not in allowed_actions:
                    return False
            elif action != allowed_actions:
                return False

        # Check principal_kind condition
        if "principal_kind" in conds and principal_kind is not None:
            allowed_kinds = conds["principal_kind"]
            if isinstance(allowed_kinds, list):
                if principal_kind not in allowed_kinds:
                    return False
            elif principal_kind != allowed_kinds:
                return False

        # Check object_type condition
        if "object_type" in conds and object_type is not None:
            allowed_types = conds["object_type"]
            if isinstance(allowed_types, list):
                if object_type not in allowed_types:
                    return False
            elif object_type != allowed_types:
                return False

        # Check scope_id condition
        if "scope_id" in conds and scope_id is not None:
            allowed_scopes = conds["scope_id"]
            if isinstance(allowed_scopes, list):
                if scope_id not in allowed_scopes:
                    return False
            elif scope_id != allowed_scopes:
                return False

        return True
