"""Rule compiler — builds efficient lookup structures from rules + bindings."""

from __future__ import annotations

import json
from typing import Any

from scoped.rules.models import (
    BindingTargetType,
    Rule,
    RuleBinding,
    binding_from_row,
    rule_from_row,
)
from scoped.storage.interface import StorageBackend
from scoped.types import Lifecycle


class CompiledRuleSet:
    """Pre-indexed rules for fast evaluation.

    Rules indexed by binding target for O(1) lookup during evaluation.
    """

    def __init__(self) -> None:
        # target_type -> target_id -> list of (rule, binding)
        self._index: dict[str, dict[str, list[tuple[Rule, RuleBinding]]]] = {}
        self._all_rules: list[Rule] = []

    def add(self, rule: Rule, binding: RuleBinding) -> None:
        key = binding.target_type.value
        if key not in self._index:
            self._index[key] = {}
        target_map = self._index[key]
        if binding.target_id not in target_map:
            target_map[binding.target_id] = []
        target_map[binding.target_id].append((rule, binding))
        self._all_rules.append(rule)

    def lookup(
        self,
        target_type: BindingTargetType,
        target_id: str,
    ) -> list[Rule]:
        """Get rules bound to a specific target, sorted by priority (high first)."""
        key = target_type.value
        entries = self._index.get(key, {}).get(target_id, [])
        rules = [r for r, _ in entries]
        rules.sort(key=lambda r: r.priority, reverse=True)
        return rules

    def all_rules(self) -> list[Rule]:
        return list(self._all_rules)

    @property
    def size(self) -> int:
        return len(self._all_rules)


class RuleCompiler:
    """Compiles rules and bindings from the database into a CompiledRuleSet."""

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def compile(
        self,
        *,
        rule_type: str | None = None,
        scope_id: str | None = None,
    ) -> CompiledRuleSet:
        """Compile active rules into an indexed set.

        Optionally filter by rule_type or scope binding.
        """
        ruleset = CompiledRuleSet()

        # Load active rules
        clauses = ["r.lifecycle = ?"]
        params: list[Any] = [Lifecycle.ACTIVE.name]

        if rule_type is not None:
            clauses.append("r.rule_type = ?")
            params.append(rule_type)

        where = " AND ".join(clauses)
        rule_rows = self._backend.fetch_all(
            f"SELECT * FROM rules r WHERE {where} ORDER BY r.priority DESC",
            tuple(params),
        )

        rule_map = {}
        for row in rule_rows:
            rule = rule_from_row(row)
            rule_map[rule.id] = rule

        if not rule_map:
            return ruleset

        # Load active bindings for those rules
        placeholders = ",".join("?" for _ in rule_map)
        binding_clauses = [f"rule_id IN ({placeholders})", "lifecycle = ?"]
        binding_params: list[Any] = list(rule_map.keys()) + [Lifecycle.ACTIVE.name]

        if scope_id is not None:
            binding_clauses.append("(target_type = ? AND target_id = ?)")
            binding_params.extend(["scope", scope_id])

        binding_where = " AND ".join(binding_clauses)
        binding_rows = self._backend.fetch_all(
            f"SELECT * FROM rule_bindings WHERE {binding_where}",
            tuple(binding_params),
        )

        for brow in binding_rows:
            binding = binding_from_row(brow)
            rule = rule_map.get(binding.rule_id)
            if rule is not None:
                ruleset.add(rule, binding)

        return ruleset
