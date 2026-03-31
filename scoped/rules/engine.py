"""Rule evaluation engine — deny-overrides model.

Evaluates rules for (principal, action, target, scope) tuples.
"""

from __future__ import annotations

import json
from typing import Any

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
        conditions: dict[str, Any] | None = None,
        priority: int = 0,
        description: str = "",
        created_by: str,
    ) -> Rule:
        """Create a new rule with its first version."""
        ts = now_utc()
        rule_id = generate_id()
        conds = conditions or {}

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

        self._backend.execute(
            "INSERT INTO rules "
            "(id, name, description, rule_type, effect, priority, conditions_json, "
            "registry_entry_id, created_at, created_by, lifecycle, current_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rule.id, rule.name, rule.description,
                rule.rule_type.value, rule.effect.value,
                rule.priority, json.dumps(conds),
                rule.registry_entry_id, rule.created_at.isoformat(),
                rule.created_by, rule.lifecycle.name, rule.current_version,
            ),
        )

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
        row = self._backend.fetch_one(
            "SELECT * FROM rules WHERE id = ?", (rule_id,),
        )
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
        clauses: list[str] = []
        params: list[Any] = []

        if rule_type is not None:
            clauses.append("rule_type = ?")
            params.append(rule_type.value)
        if effect is not None:
            clauses.append("effect = ?")
            params.append(effect.value)
        if active_only:
            clauses.append("lifecycle = ?")
            params.append(Lifecycle.ACTIVE.name)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._backend.fetch_all(
            f"SELECT * FROM rules{where} ORDER BY priority DESC",
            tuple(params),
        )
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

        self._backend.execute(
            "UPDATE rules SET conditions_json = ?, effect = ?, priority = ?, "
            "current_version = ? WHERE id = ?",
            (
                json.dumps(rule.conditions), rule.effect.value,
                rule.priority, rule.current_version, rule_id,
            ),
        )

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

        self._backend.execute(
            "UPDATE rules SET lifecycle = ? WHERE id = ?",
            (Lifecycle.ARCHIVED.name, rule_id),
        )
        self._backend.execute(
            "UPDATE rule_bindings SET lifecycle = ? WHERE rule_id = ? AND lifecycle = ?",
            (Lifecycle.ARCHIVED.name, rule_id, Lifecycle.ACTIVE.name),
        )

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
        rows = self._backend.fetch_all(
            "SELECT * FROM rule_versions WHERE rule_id = ? ORDER BY version ASC",
            (rule_id,),
        )
        return [rule_version_from_row(r) for r in rows]

    def _create_version(
        self,
        rule: Rule,
        *,
        change_reason: str = "",
        created_by: str | None = None,
    ) -> RuleVersion:
        ts = now_utc()
        ver_id = generate_id()
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
        self._backend.execute(
            "INSERT INTO rule_versions "
            "(id, rule_id, version, conditions_json, effect, priority, "
            "created_at, created_by, change_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ver.id, ver.rule_id, ver.version,
                json.dumps(ver.conditions), ver.effect.value, ver.priority,
                ver.created_at.isoformat(), ver.created_by, ver.change_reason,
            ),
        )
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
        binding_id = generate_id()
        binding = RuleBinding(
            id=binding_id,
            rule_id=rule_id,
            target_type=target_type,
            target_id=target_id,
            bound_at=ts,
            bound_by=bound_by,
        )

        self._backend.execute(
            "INSERT INTO rule_bindings "
            "(id, rule_id, target_type, target_id, bound_at, bound_by, lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                binding.id, binding.rule_id, binding.target_type.value,
                binding.target_id, binding.bound_at.isoformat(),
                binding.bound_by, binding.lifecycle.name,
            ),
        )

        return binding

    def unbind_rule(
        self,
        rule_id: str,
        *,
        target_type: BindingTargetType,
        target_id: str,
    ) -> bool:
        """Remove a rule binding. Returns True if removed."""
        row = self._backend.fetch_one(
            "SELECT 1 FROM rule_bindings "
            "WHERE rule_id = ? AND target_type = ? AND target_id = ? AND lifecycle = ?",
            (rule_id, target_type.value, target_id, Lifecycle.ACTIVE.name),
        )
        if row is None:
            return False
        self._backend.execute(
            "UPDATE rule_bindings SET lifecycle = ? "
            "WHERE rule_id = ? AND target_type = ? AND target_id = ? AND lifecycle = ?",
            (Lifecycle.ARCHIVED.name, rule_id, target_type.value, target_id, Lifecycle.ACTIVE.name),
        )
        return True

    def get_bindings(
        self,
        rule_id: str,
        *,
        active_only: bool = True,
    ) -> list[RuleBinding]:
        clauses = ["rule_id = ?"]
        params: list[Any] = [rule_id]
        if active_only:
            clauses.append("lifecycle = ?")
            params.append(Lifecycle.ACTIVE.name)
        where = " AND ".join(clauses)
        rows = self._backend.fetch_all(
            f"SELECT * FROM rule_bindings WHERE {where}",
            tuple(params),
        )
        return [binding_from_row(r) for r in rows]

    def get_target_bindings(
        self,
        target_type: BindingTargetType,
        target_id: str,
        *,
        active_only: bool = True,
    ) -> list[RuleBinding]:
        """Get all bindings for a specific target."""
        clauses = ["target_type = ?", "target_id = ?"]
        params: list[Any] = [target_type.value, target_id]
        if active_only:
            clauses.append("lifecycle = ?")
            params.append(Lifecycle.ACTIVE.name)
        where = " AND ".join(clauses)
        rows = self._backend.fetch_all(
            f"SELECT * FROM rule_bindings WHERE {where}",
            tuple(params),
        )
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
        rows = self._backend.fetch_all(
            "SELECT r.* FROM rules r "
            "JOIN rule_bindings rb ON r.id = rb.rule_id "
            "WHERE rb.target_type = ? AND rb.target_id = ? "
            "AND rb.lifecycle = ? AND r.lifecycle = ?",
            (target_type.value, target_id, Lifecycle.ACTIVE.name, Lifecycle.ACTIVE.name),
        )
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
