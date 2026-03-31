"""Layer 5: Rule Engine.

Rules modify what the scoping engine allows.  Deny-overrides model:
any DENY wins over any number of ALLOWs.  Default-deny when no rules match.
"""

from scoped.rules.compiler import CompiledRuleSet, RuleCompiler
from scoped.rules.engine import RuleEngine, RuleStore
from scoped.rules.features import FeatureFlagConfig, FeatureFlagEngine, FeatureFlagResult
from scoped.rules.models import (
    BindingTargetType,
    EvaluationResult,
    Rule,
    RuleBinding,
    RuleEffect,
    RuleType,
    RuleVersion,
)
from scoped.rules.quotas import QuotaChecker, QuotaConfig, QuotaResult
from scoped.rules.rate_limit import RateLimitChecker, RateLimitConfig, RateLimitResult
from scoped.rules.redaction import (
    FieldRedaction,
    RedactionEngine,
    RedactionResult,
    RedactionStrategy,
)

__all__ = [
    "BindingTargetType",
    "CompiledRuleSet",
    "EvaluationResult",
    "FeatureFlagConfig",
    "FeatureFlagEngine",
    "FeatureFlagResult",
    "FieldRedaction",
    "QuotaChecker",
    "QuotaConfig",
    "QuotaResult",
    "RateLimitChecker",
    "RateLimitConfig",
    "RateLimitResult",
    "RedactionEngine",
    "RedactionResult",
    "RedactionStrategy",
    "Rule",
    "RuleBinding",
    "RuleCompiler",
    "RuleEffect",
    "RuleEngine",
    "RuleStore",
    "RuleType",
    "RuleVersion",
]
