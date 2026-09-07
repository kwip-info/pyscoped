"""Feature-flag engine — gate capabilities at scope/principal/environment level.

Feature-flag rules use the ``conditions`` dict with a ``feature_flag`` key:

    {
        "feature_flag": {
            "feature_name": "dark_mode",
            "enabled": true,
            "rollout_percentage": 100,
        }
    }

Flags can be scoped (bound to specific scopes, principals, or environments).
The engine evaluates flags by checking all matching rules and applying
the highest-priority match.

A flag with ``enabled=false`` at a higher priority overrides ``enabled=true``
at a lower priority (explicit disable wins via priority, not deny-overrides).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from scoped.rules.models import Rule, RuleType
from scoped.storage.interface import StorageBackend
from scoped.types import Lifecycle


@dataclass(frozen=True, slots=True)
class FeatureFlagConfig:
    """Parsed feature-flag configuration from a rule."""
    feature_name: str
    enabled: bool
    rollout_percentage: int

    @classmethod
    def from_rule(cls, rule: Rule) -> FeatureFlagConfig | None:
        cfg = rule.conditions.get("feature_flag")
        if cfg is None:
            return None
        return cls(
            feature_name=cfg["feature_name"],
            enabled=cfg.get("enabled", True),
            rollout_percentage=cfg.get("rollout_percentage", 100),
        )


@dataclass(frozen=True, slots=True)
class FeatureFlagResult:
    """Outcome of a feature-flag check."""
    enabled: bool
    feature_name: str
    rule_id: str | None
    rollout_percentage: int


class FeatureFlagEngine:
    """Evaluate feature flags from rules.

    Usage::

        engine = FeatureFlagEngine(backend, rules)
        result = engine.is_enabled("dark_mode", principal_id="user-1")
        if result.enabled:
            ...
    """

    def __init__(
        self,
        backend: StorageBackend,
        rules: list[Rule],
    ) -> None:
        self._backend = backend
        self._rules = [
            r for r in rules
            if r.rule_type == RuleType.FEATURE_FLAG and r.is_active
        ]

    def is_enabled(
        self,
        feature_name: str,
        *,
        principal_id: str | None = None,
        scope_id: str | None = None,
    ) -> FeatureFlagResult:
        """Check if a feature flag is enabled.

        Evaluates all matching feature-flag rules sorted by priority
        (highest first). The first match determines the result.

        If ``rollout_percentage`` < 100, uses deterministic hashing of
        ``principal_id + feature_name`` to decide inclusion.
        """
        matching = self._find_matching(feature_name, scope_id=scope_id)

        if not matching:
            return FeatureFlagResult(
                enabled=False,
                feature_name=feature_name,
                rule_id=None,
                rollout_percentage=0,
            )

        # Highest priority wins
        matching.sort(key=lambda r: r.priority, reverse=True)
        winner = matching[0]
        config = FeatureFlagConfig.from_rule(winner)
        if config is None:
            return FeatureFlagResult(
                enabled=False,
                feature_name=feature_name,
                rule_id=winner.id,
                rollout_percentage=0,
            )

        enabled = config.enabled
        if enabled and config.rollout_percentage < 100 and principal_id:
            enabled = self._in_rollout(
                feature_name, principal_id, config.rollout_percentage,
            )

        return FeatureFlagResult(
            enabled=enabled,
            feature_name=feature_name,
            rule_id=winner.id,
            rollout_percentage=config.rollout_percentage,
        )

    def list_flags(self) -> list[FeatureFlagConfig]:
        """Return all known feature flag configurations."""
        flags: list[FeatureFlagConfig] = []
        seen: set[str] = set()
        for rule in self._rules:
            config = FeatureFlagConfig.from_rule(rule)
            if config and config.feature_name not in seen:
                seen.add(config.feature_name)
                flags.append(config)
        return flags

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _find_matching(
        self,
        feature_name: str,
        *,
        scope_id: str | None,
    ) -> list[Rule]:
        """Find all rules that match the given feature name."""
        result: list[Rule] = []
        for rule in self._rules:
            config = FeatureFlagConfig.from_rule(rule)
            if config is None:
                continue
            if config.feature_name != feature_name:
                continue
            # Check scope condition if present
            conds = rule.conditions
            if "scope_id" in conds and scope_id is not None:
                allowed = conds["scope_id"]
                if isinstance(allowed, list):
                    if scope_id not in allowed:
                        continue
                elif scope_id != allowed:
                    continue
            result.append(rule)
        return result

    @staticmethod
    def _in_rollout(
        feature_name: str,
        principal_id: str,
        percentage: int,
    ) -> bool:
        """Deterministic rollout check using hash of principal + feature."""
        key = f"{principal_id}:{feature_name}"
        h = hashlib.sha256(key.encode()).hexdigest()
        bucket = int(h[:8], 16) % 100
        return bucket < percentage
