# A3: Rule Extensions

**Extends:** Layer 5 (Rules)

## Purpose

The core rule engine has 5 rule types (access, sharing, visibility, ownership, constraint). This extension adds 4 critical policy patterns: redaction, rate limiting, quotas, and feature flags. Each is implemented as a new rule type with its own engine that integrates with the existing rule infrastructure.

## New Rule Types

### REDACTION — Field-Level Data Masking

Redaction rules declare which fields of an object type should be masked, replaced, or omitted when the viewer matches certain conditions.

**Strategies:**

| Strategy | Behavior |
|----------|----------|
| `mask` | Replace characters with a mask character (e.g., `****1234`), optionally keeping last N visible |
| `replace` | Swap the entire value with a fixed replacement string (e.g., `[REDACTED]`) |
| `omit` | Remove the field from the output entirely |

**Conditions format:**
```json
{
    "object_type": "CreditCard",
    "principal_kind": ["viewer", "auditor"],
    "redactions": {
        "card_number": {"strategy": "mask", "mask_char": "*", "visible_chars": 4},
        "cvv": {"strategy": "omit"},
        "holder_name": {"strategy": "replace", "replacement": "[REDACTED]"}
    }
}
```

**Key class:** `RedactionEngine` — collects matching redaction rules, applies strategies to object data, returns redacted copy (never mutates original).

### RATE_LIMIT — Action Throttling

Rate-limit rules throttle actions per principal, scope, or time window by counting matching entries in the audit trail.

**Conditions format:**
```json
{
    "action": ["create", "update"],
    "rate_limit": {
        "max_count": 100,
        "window_seconds": 3600
    }
}
```

**Key class:** `RateLimitChecker` — counts recent audit entries within the window, returns `RateLimitResult` with `allowed`, `current_count`, `max_count`, `resets_at`.

### QUOTA — Resource Count Limits

Quota rules enforce hard limits on resource counts per scope or principal.

**Conditions format:**
```json
{
    "object_type": "Document",
    "quota": {
        "max_count": 1000,
        "count_table": "scoped_objects",
        "count_column": "object_type",
        "count_value": "Document"
    }
}
```

Simplified form (auto-detects table/column from `object_type`):
```json
{
    "object_type": "Document",
    "quota": {"max_count": 1000}
}
```

**Key class:** `QuotaChecker` — counts current resources, returns `QuotaResult` with `allowed`, `current_count`, `max_count`, `remaining`.

### FEATURE_FLAG — Capability Gating

Feature-flag rules gate capabilities at scope, principal, or environment level with optional percentage-based rollout.

**Conditions format:**
```json
{
    "feature_flag": {
        "feature_name": "dark_mode",
        "enabled": true,
        "rollout_percentage": 100
    }
}
```

Rollout uses deterministic hashing of `(feature_name, principal_id)` so a principal always gets the same result for a given percentage.

**Key class:** `FeatureFlagEngine` — evaluates flag rules by priority, supports `is_enabled(feature_name, principal_id, scope_id)` queries, returns all flags with `list_flags()`.

## Files

```
scoped/rules/
    builtins.py        # Updated with REDACTION, RATE_LIMIT, QUOTA, FEATURE_FLAG types
    redaction.py       # RedactionEngine, RedactionStrategy, RedactionConfig
    rate_limit.py      # RateLimitChecker, RateLimitConfig, RateLimitResult
    quotas.py          # QuotaChecker, QuotaConfig, QuotaResult
    features.py        # FeatureFlagEngine, FeatureFlagConfig, FlagState
```

## Invariants

1. Redaction never mutates the original data — always returns a copy.
2. Rate limits are enforced by counting actual audit trail entries (not in-memory counters).
3. Quota checks are point-in-time counts (not cached).
4. Feature flag rollout is deterministic per principal — same input always produces same result.
5. All four rule types integrate with the existing deny-overrides model where applicable.
