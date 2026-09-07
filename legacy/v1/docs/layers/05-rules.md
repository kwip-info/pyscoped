# Layer 5: Rule Engine

## Purpose

Rules are the policy layer. They answer: **is this allowed?**

Scopes define structure (who is where). Rules define behavior (what can happen). A scope might include a user as a member, but a rule might deny them write access after hours. A scope might allow projection, but a rule might prohibit sharing outside the organization.

Rules are the mechanism by which applications express their security, compliance, and business policies within the Scoped framework.

## Core Concepts

### Rule

A rule is a condition-effect pair: "if this condition is true, apply this effect."

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `name` | Human-readable label |
| `rule_type` | What kind of policy: access, sharing, visibility, ownership, constraint |
| `effect` | ALLOW or DENY |
| `priority` | Higher = evaluated first |
| `conditions_json` | When does this rule apply? (principal kind, scope, time, object type, etc.) |
| `current_version` | Rules are versioned — changes create new versions |

### Deny-Overrides Model

When multiple rules apply to the same action:

1. All matching rules are collected
2. If ANY rule has effect DENY, the action is denied
3. If no DENY rules match and at least one ALLOW matches, the action is allowed
4. If no rules match at all, the action is denied (default-deny)

This means security is the default. Access must be explicitly granted, and any single DENY rule wins over any number of ALLOWs.

### Rule Types

**Access rules** — grant/deny CRUD operations on objects within a scope.
- "Members of scope X can read objects of type Document"
- "Only admins can delete objects in scope Y"

**Sharing rules** — control whether objects can be projected between scopes.
- "Objects in scope Enterprise cannot be projected into scopes outside the enterprise"
- "Only owners can project objects into external scopes"

**Visibility rules** — control what audit traces a principal can see.
- "Users can only see trace entries for actions they performed"
- "Admins can see all traces within their scope"

**Ownership rules** — define default ownership cascading.
- "Objects created within scope OrgA are co-owned by the org principal"
- "When a user leaves a scope, ownership of their objects transfers to the scope owner"

**Constraint rules** — arbitrary predicates.
- "No write operations between 10 PM and 6 AM"
- "Objects with classification=sensitive cannot be projected into scopes with external members"

### Rule Binding

Rules are attached to targets:

| Target Type | Meaning |
|-------------|---------|
| `scope` | Rule applies within this scope |
| `principal` | Rule applies to this specific principal |
| `object_type` | Rule applies to all objects of this type |
| `object` | Rule applies to a specific object |
| `environment` | Rule applies within this environment |
| `connector` | Rule applies to traffic through this connector |

Multiple bindings can exist for one rule. A rule bound to both a scope and an object type means "this rule applies to objects of this type within this scope."

### Rule Versioning

Changing a rule creates a new `RuleVersion`. The old version is retained. This means:
- You can audit what the rules were at any point in time
- You can roll back rule changes (Layer 7)
- Rule change history is part of the compliance record

## How It Connects

### To Layer 1 (Registry)
Rules are registered constructs. Rule bindings reference registered targets by URN or ID.

### To Layer 4 (Tenancy)
Rules modify what scopes allow. The tenancy engine calls the rule engine to evaluate access. Sharing rules control whether projections can cross scope boundaries.

### To Layer 6 (Audit)
Rule evaluations are traced. Every access check that evaluates rules produces a trace entry recording which rules matched, what effect they had, and the outcome. Visibility rules govern what traces a principal can see.

### To Layer 7 (Temporal)
Rule changes are rollbackable. Rolling back a rule change restores the previous version. The temporal layer checks with the rule engine to see if a rollback is permitted.

### To Layer 8 (Environments)
Rules can be bound to environments. "Within this throwaway environment, all members have full write access" is a valid rule binding.

### To Layer 9 (Flow)
Stage transitions can be rule-governed. "To move from 'review' to 'approved', the object must have at least 2 approvals from admin principals" is a constraint rule bound to a stage.

### To Layer 10 (Deployments)
Deployment gate checks evaluate rules. "This deployment is only allowed if all sharing rules are satisfied and no DENY rules apply to the deployment target."

### To Layer 11 (Secrets)
Secret access rules are a subset of access rules. Secret policies (rotation requirements, access restrictions) are implemented as rules bound to secrets or secret classifications.

### To Layer 12 (Integrations)
Plugin permissions are evaluated as rules. "This plugin is allowed to read objects of type X within scope Y" is a rule binding.

### To Layer 13 (Connector)
Connector policies are implemented as rules. "Only objects of type Document can flow through this connector" and "no secrets can flow through any connector" are sharing rules bound to connectors.

## Extensions

This layer has been extended with:

- **[A3: Rule Extensions](../extensions/A3-rule-extensions.md)** — Four new rule types:
  - **REDACTION** — field-level data masking (mask, replace, omit strategies)
  - **RATE_LIMIT** — action throttling per principal/scope/time window
  - **QUOTA** — hard limits on resource counts
  - **FEATURE_FLAG** — capability gating with percentage-based rollout

## Files

```
scoped/rules/
    __init__.py
    models.py        # Rule, RuleVersion, RuleBinding, RuleEffect
    engine.py        # Evaluate rules for (principal, action, target, scope) tuples
    builtins.py      # Built-in rule types (9 total: 5 core + 4 extensions)
    compiler.py      # Compile rule sets into efficient lookup structures
    redaction.py     # [A3] RedactionEngine — field-level data masking
    rate_limit.py    # [A3] RateLimitChecker — action throttling
    quotas.py        # [A3] QuotaChecker — resource count limits
    features.py      # [A3] FeatureFlagEngine — capability gating
```

## Schema

```sql
CREATE TABLE rules (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    rule_type       TEXT NOT NULL,
    effect          TEXT NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 0,
    conditions_json TEXT NOT NULL DEFAULT '{}',
    registry_entry_id TEXT,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    current_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE rule_versions (
    id              TEXT PRIMARY KEY,
    rule_id         TEXT NOT NULL REFERENCES rules(id),
    version         INTEGER NOT NULL,
    conditions_json TEXT NOT NULL,
    effect          TEXT NOT NULL,
    priority        INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    change_reason   TEXT NOT NULL DEFAULT '',
    UNIQUE(rule_id, version)
);

CREATE TABLE rule_bindings (
    id              TEXT PRIMARY KEY,
    rule_id         TEXT NOT NULL REFERENCES rules(id),
    target_type     TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    bound_at        TEXT NOT NULL,
    bound_by        TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    UNIQUE(rule_id, target_type, target_id)
);
```

## Invariants

1. Deny always wins. Any DENY overrides any number of ALLOWs.
2. Default-deny. If no rules match, the action is denied.
3. Rule changes are versioned and traced.
4. Rule evaluations are traced (for audit completeness).
5. Rules are registered constructs with lifecycles.
