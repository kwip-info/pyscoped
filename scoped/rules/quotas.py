"""Quota checker — hard limits on resource counts.

Quota rules use the ``conditions`` dict with a ``quota`` key:

    {
        "object_type": "Document",
        "quota": {
            "max_count": 1000,
            "count_table": "objects",        # table to count
            "count_column": "object_type",   # column to filter on
            "count_value": "Document",       # value to match
        }
    }

For scope-level quotas (e.g., max objects per scope), the ``scope_id``
condition filters counting to a specific scope.

Simplified form for common cases::

    {
        "object_type": "Document",
        "quota": {"max_count": 1000}
    }

When ``count_table`` is omitted, defaults to ``"objects"`` with
``count_column`` = ``"object_type"`` and ``count_value`` from the
``object_type`` condition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scoped.rules.models import Rule, RuleType
from scoped.storage.interface import StorageBackend


@dataclass(frozen=True, slots=True)
class QuotaConfig:
    """Parsed quota configuration from a rule."""
    max_count: int
    count_table: str
    count_column: str
    count_value: str
    scope_column: str | None

    @classmethod
    def from_rule(cls, rule: Rule) -> QuotaConfig | None:
        cfg = rule.conditions.get("quota")
        if cfg is None:
            return None

        # Derive defaults from the rule conditions
        object_type = rule.conditions.get("object_type")
        if isinstance(object_type, list):
            object_type = object_type[0] if object_type else None

        return cls(
            max_count=cfg["max_count"],
            count_table=cfg.get("count_table", "scoped_objects"),
            count_column=cfg.get("count_column", "object_type"),
            count_value=cfg.get("count_value", object_type or ""),
            scope_column=cfg.get("scope_column"),
        )


@dataclass(frozen=True, slots=True)
class QuotaResult:
    """Outcome of a quota check."""
    allowed: bool
    current_count: int
    max_count: int
    rule_id: str
    object_type: str


class QuotaChecker:
    """Check whether creating a resource would exceed its quota.

    Usage::

        checker = QuotaChecker(backend, rules)
        result = checker.check(object_type="Document", scope_id="scope-1")
        if not result.allowed:
            raise QuotaExceededError(...)
    """

    def __init__(
        self,
        backend: StorageBackend,
        rules: list[Rule],
    ) -> None:
        self._backend = backend
        self._rules = [
            r for r in rules
            if r.rule_type == RuleType.QUOTA and r.is_active
        ]

    def check(
        self,
        *,
        object_type: str,
        scope_id: str | None = None,
    ) -> QuotaResult | None:
        """Check all matching quota rules. Returns the first violation, or None."""
        for rule in self._rules:
            if not self._type_matches(rule, object_type):
                continue

            config = QuotaConfig.from_rule(rule)
            if config is None:
                continue

            count = self._count_resources(config, scope_id=scope_id)

            if count >= config.max_count:
                return QuotaResult(
                    allowed=False,
                    current_count=count,
                    max_count=config.max_count,
                    rule_id=rule.id,
                    object_type=object_type,
                )

        return None

    def check_or_raise(
        self,
        *,
        object_type: str,
        scope_id: str | None = None,
    ) -> None:
        """Check quotas and raise ``QuotaExceededError`` on violation."""
        from scoped.exceptions import QuotaExceededError

        result = self.check(object_type=object_type, scope_id=scope_id)
        if result is not None and not result.allowed:
            raise QuotaExceededError(
                f"Quota exceeded for {object_type}: "
                f"{result.current_count}/{result.max_count}",
                context={
                    "rule_id": result.rule_id,
                    "current_count": result.current_count,
                    "max_count": result.max_count,
                    "object_type": object_type,
                },
            )

    def get_usage(
        self,
        *,
        object_type: str,
        scope_id: str | None = None,
    ) -> dict[str, Any]:
        """Return usage info for all matching quota rules."""
        usage: dict[str, Any] = {}
        for rule in self._rules:
            if not self._type_matches(rule, object_type):
                continue
            config = QuotaConfig.from_rule(rule)
            if config is None:
                continue
            count = self._count_resources(config, scope_id=scope_id)
            usage[rule.id] = {
                "current_count": count,
                "max_count": config.max_count,
                "remaining": max(0, config.max_count - count),
                "object_type": object_type,
            }
        return usage

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _type_matches(rule: Rule, object_type: str) -> bool:
        conds = rule.conditions
        if "object_type" not in conds:
            return True
        allowed = conds["object_type"]
        if isinstance(allowed, list):
            return object_type in allowed
        return object_type == allowed

    def _count_resources(
        self,
        config: QuotaConfig,
        *,
        scope_id: str | None,
    ) -> int:
        """Count existing resources matching the quota config."""
        # Allowlist of tables we can count against to prevent injection
        _ALLOWED_TABLES = {
            "scoped_objects", "scopes", "environments", "secrets",
            "contracts", "rules", "integrations", "plugins",
        }
        table = config.count_table
        if table not in _ALLOWED_TABLES:
            return 0

        clauses = [f"{config.count_column} = ?"]
        params: list[Any] = [config.count_value]

        if scope_id is not None and config.scope_column:
            clauses.append(f"{config.scope_column} = ?")
            params.append(scope_id)
        elif scope_id is not None and table == "objects":
            clauses.append("scope_id = ?")
            params.append(scope_id)

        # Exclude archived entries
        clauses.append("lifecycle = ?")
        params.append("ACTIVE")

        where = " AND ".join(clauses)
        row = self._backend.fetch_one(
            f"SELECT COUNT(*) AS cnt FROM {table} WHERE {where}",
            tuple(params),
        )
        return row["cnt"] if row else 0
