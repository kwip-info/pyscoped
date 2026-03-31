"""Rate-limit checker — throttle actions per principal/scope/time window.

Rate-limit rules use the ``conditions`` dict with a ``rate_limit`` key:

    {
        "action": ["create", "update"],
        "rate_limit": {
            "max_count": 100,
            "window_seconds": 3600,
        }
    }

Enforcement counts matching entries in the audit trail within the time
window before allowing an action.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from scoped.rules.models import Rule, RuleType
from scoped.storage.interface import StorageBackend
from scoped.types import now_utc


@dataclass(frozen=True, slots=True)
class RateLimitConfig:
    """Parsed rate-limit configuration from a rule."""
    max_count: int
    window_seconds: int

    @classmethod
    def from_rule(cls, rule: Rule) -> RateLimitConfig | None:
        cfg = rule.conditions.get("rate_limit")
        if cfg is None:
            return None
        return cls(
            max_count=cfg["max_count"],
            window_seconds=cfg["window_seconds"],
        )


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Outcome of a rate-limit check."""
    allowed: bool
    current_count: int
    max_count: int
    window_seconds: int
    rule_id: str
    retry_after_seconds: int | None = None


class RateLimitChecker:
    """Check whether an action would exceed a rate limit.

    Rate limits are defined via rules of type ``RATE_LIMIT``.
    The checker counts matching audit trail entries within the window.

    Usage::

        checker = RateLimitChecker(backend, rules)
        result = checker.check(
            action="create",
            principal_id="user-1",
            scope_id="scope-1",
        )
        if not result.allowed:
            raise RateLimitExceededError(...)
    """

    def __init__(
        self,
        backend: StorageBackend,
        rules: list[Rule],
    ) -> None:
        self._backend = backend
        self._rules = [
            r for r in rules
            if r.rule_type == RuleType.RATE_LIMIT and r.is_active
        ]

    def check(
        self,
        *,
        action: str,
        principal_id: str | None = None,
        scope_id: str | None = None,
    ) -> RateLimitResult | None:
        """Check all matching rate-limit rules. Returns the first violation, or None."""
        for rule in self._rules:
            if not self._action_matches(rule, action):
                continue

            config = RateLimitConfig.from_rule(rule)
            if config is None:
                continue

            count = self._count_actions(
                action=action,
                principal_id=principal_id,
                scope_id=scope_id,
                window_seconds=config.window_seconds,
            )

            if count >= config.max_count:
                return RateLimitResult(
                    allowed=False,
                    current_count=count,
                    max_count=config.max_count,
                    window_seconds=config.window_seconds,
                    rule_id=rule.id,
                    retry_after_seconds=config.window_seconds,
                )

        # No violations — return allowed result for the last matched rule,
        # or None if no rules matched.
        return None

    def check_or_raise(
        self,
        *,
        action: str,
        principal_id: str | None = None,
        scope_id: str | None = None,
    ) -> None:
        """Check rate limits and raise ``RateLimitExceededError`` on violation."""
        from scoped.exceptions import RateLimitExceededError

        result = self.check(
            action=action,
            principal_id=principal_id,
            scope_id=scope_id,
        )
        if result is not None and not result.allowed:
            raise RateLimitExceededError(
                f"Rate limit exceeded: {result.current_count}/{result.max_count} "
                f"in {result.window_seconds}s window",
                context={
                    "rule_id": result.rule_id,
                    "current_count": result.current_count,
                    "max_count": result.max_count,
                    "window_seconds": result.window_seconds,
                },
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _action_matches(rule: Rule, action: str) -> bool:
        conds = rule.conditions
        if "action" not in conds:
            return True  # no action filter = matches all
        allowed = conds["action"]
        if isinstance(allowed, list):
            return action in allowed
        return action == allowed

    def _count_actions(
        self,
        *,
        action: str,
        principal_id: str | None,
        scope_id: str | None,
        window_seconds: int,
    ) -> int:
        """Count audit trail entries matching the criteria within the window."""
        cutoff = now_utc() - timedelta(seconds=window_seconds)
        cutoff_iso = cutoff.isoformat()

        clauses = ["action = ?", "timestamp >= ?"]
        params: list[Any] = [action, cutoff_iso]

        if principal_id is not None:
            clauses.append("actor_id = ?")
            params.append(principal_id)
        if scope_id is not None:
            clauses.append("scope_id = ?")
            params.append(scope_id)

        where = " AND ".join(clauses)
        row = self._backend.fetch_one(
            f"SELECT COUNT(*) AS cnt FROM audit_trail WHERE {where}",
            tuple(params),
        )
        return row["cnt"] if row else 0
