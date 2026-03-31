# Layer 10: Deployments

## Purpose

Deployments are the **exit ramp**. When work graduates from the Scoped world into an external destination — a production system, an API, a file system, another platform — it goes through the deployment layer.

Deployments are the final flow. They're versioned, traced, gate-checked, and rollbackable.

## Core Concepts

### DeploymentTarget

A registered construct representing where deployments go.

| Field | Purpose |
|-------|---------|
| `name` | Target name ("production", "staging", "partner-api") |
| `target_type` | Application-defined: what kind of destination |
| `config_json` | Non-secret configuration for reaching the target |
| `owner_id` | Who controls this target |

Targets are abstract — the framework doesn't know how to deploy to "production." That's the application's job. The framework provides the governance: can this deployment happen? Is it authorized? Has it passed all gates?

### Deployment

A record of work being pushed to a target.

| Field | Purpose |
|-------|---------|
| `target_id` | Where it's going |
| `object_id` | Specific object being deployed (nullable for bulk) |
| `scope_id` | Scope being deployed (nullable for single object) |
| `version` | Deployment version number |
| `state` | pending → deploying → deployed / failed / rolled_back |
| `deployed_by` | Who triggered it |
| `rollback_of` | If this deployment reverses another, which one |

### Deployment Gates

Pre-deployment checks that must pass before the deployment proceeds.

| Field | Purpose |
|-------|---------|
| `deployment_id` | Which deployment |
| `gate_type` | What kind of check: stage_check, rule_check, approval, custom |
| `passed` | Did it pass? |
| `details_json` | What specifically was checked and what the result was |

Gate types:

**Stage check** — has the object reached the required stage in its pipeline?
**Rule check** — do all applicable rules allow this deployment?
**Approval** — has the required principal approved this deployment?
**Custom** — application-defined check (test suite passed, security scan clean, etc.)

All gates must pass for a deployment to proceed. Each gate check is a traced action.

### Deployment Rollback

A deployment rollback creates a new deployment record with `rollback_of` pointing to the original. This means:
- Rollback history is preserved
- Rollbacks are themselves deployments (with their own gates, traces, etc.)
- You can roll back a rollback

## How It Connects

### To Layer 5 (Rules)
Deployment authorization is rule-governed. Gate checks evaluate rules. Deployment targets can have rules bound to them.

### To Layer 6 (Audit)
Every deployment action — create, gate check, deploy, fail, rollback — is traced.

### To Layer 7 (Temporal)
Deployment rollbacks are a specific case of temporal rollback. The temporal layer can identify what state existed before a deployment and help restore it.

### To Layer 9 (Flow)
Deployments are the terminal stage in a pipeline. Reaching the final stage might auto-trigger a deployment. The deployment layer checks that all flow requirements are satisfied.

### To Layer 11 (Secrets)
Deployment targets might require credentials (connection strings, API keys). These are referenced via secret refs, not stored in `config_json`.

### To Layer 12 (Integrations)
The actual execution of a deployment often goes through an integration — a connection to the external system. The deployment layer governs the decision; the integration layer handles the connection.

## Files

```
scoped/deployments/
    __init__.py
    models.py          # Deployment, DeploymentTarget, DeploymentPolicy
    executor.py        # Execute deployments (abstract — app-specific)
    gates.py           # Pre-deployment gate checks
    rollback.py        # Deployment rollback mechanics
```

## Schema

```sql
CREATE TABLE deployment_targets (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    target_type     TEXT NOT NULL,
    config_json     TEXT NOT NULL DEFAULT '{}',
    owner_id        TEXT NOT NULL REFERENCES principals(id),
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE deployments (
    id              TEXT PRIMARY KEY,
    target_id       TEXT NOT NULL REFERENCES deployment_targets(id),
    object_id       TEXT REFERENCES scoped_objects(id),
    scope_id        TEXT REFERENCES scopes(id),
    version         INTEGER NOT NULL DEFAULT 1,
    state           TEXT NOT NULL DEFAULT 'pending',
    deployed_at     TEXT,
    deployed_by     TEXT NOT NULL,
    rollback_of     TEXT REFERENCES deployments(id),
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE deployment_gates (
    id              TEXT PRIMARY KEY,
    deployment_id   TEXT NOT NULL REFERENCES deployments(id),
    gate_type       TEXT NOT NULL,
    passed          INTEGER NOT NULL DEFAULT 0,
    checked_at      TEXT NOT NULL,
    details_json    TEXT NOT NULL DEFAULT '{}'
);
```

## Invariants

1. All gates must pass before a deployment proceeds.
2. Deployments are versioned and traceable.
3. Deployment rollbacks are themselves deployments (fully governed).
4. Gate checks are traced actions.
5. Deployment target credentials are stored as secret refs, never in config.
