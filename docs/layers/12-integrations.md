# Layer 12: Integrations & Plugins

## Purpose

Integrations connect the Scoped world to external systems. Plugins extend the framework with new behavior. Both are first-class scoped citizens — registered, sandboxed, permission-gated, and audited.

This is the extension framework. It answers: **how does external code and external data participate in the system without breaking its guarantees?**

## Core Concepts

### Integration

A connection to an external system.

| Field | Purpose |
|-------|---------|
| `name` | Integration name ("github-acme-org", "slack-engineering") |
| `integration_type` | What kind: github, slack, database, api, custom |
| `owner_id` | Who controls this integration |
| `scope_id` | Scope this integration operates within |
| `config_json` | Non-secret configuration |
| `credentials_ref` | SecretRef ID for credentials |

Integrations are scoped — they operate within a specific scope boundary. Data flowing through an integration is traced. The integration's credentials are managed as secrets (Layer 11).

### Plugin

A code extension that plugs into framework hooks.

| Field | Purpose |
|-------|---------|
| `name` | Plugin name (unique) |
| `version` | Semantic version |
| `owner_id` | Who installed it |
| `scope_id` | Plugin's own isolation scope |
| `manifest_json` | What the plugin needs and provides |
| `state` | installed → active → suspended → uninstalled |

Plugins run in a **sandboxed execution context**. They can only access what they've been granted permission for. They have their own isolation scope — plugin data is separate from host data unless explicitly shared.

### Plugin Lifecycle

```
installed ──→ active ──→ suspended ──→ active (reactivated)
                │                          │
                └──→ uninstalled            └──→ uninstalled
```

- **installed**: Code is present, permissions are declared but not yet granted, not executing.
- **active**: Permissions granted, hooks registered, executing.
- **suspended**: Temporarily deactivated — hooks stop firing, permissions are paused but not revoked.
- **uninstalled**: Removed. Hooks deregistered, permissions revoked, plugin scope archived.

Each transition is a traced action.

### PluginManifest

A declaration of what a plugin needs and provides.

```json
{
  "permissions": [
    {"type": "scope_access", "target": "scope-123", "level": "read"},
    {"type": "object_type", "target": "Document", "operations": ["read", "create"]},
    {"type": "secret_access", "target": "api-key-ref", "level": "resolve"},
    {"type": "hook", "target": "post_object_create"}
  ],
  "provides": {
    "kinds": ["WEBHOOK"],
    "hooks": ["post_object_create", "pre_deployment"]
  }
}
```

The manifest is reviewed at install time. Permissions are granted explicitly — the plugin doesn't get anything it didn't ask for, and it doesn't get anything the installer didn't approve.

### PluginHook

Extension points the framework exposes.

| Field | Purpose |
|-------|---------|
| `plugin_id` | Which plugin |
| `hook_point` | When to fire: pre_object_create, post_scope_modify, pre_deployment, etc. |
| `handler_ref` | Registry URN of the handler function |
| `priority` | Execution order when multiple plugins hook the same point |

Hook execution is:
1. Triggered by a framework operation
2. Filtered to only plugins that registered for this hook
3. Permission-checked (does the plugin still have the required permissions?)
4. Executed in priority order
5. Traced (every hook execution produces an audit entry)
6. Sandboxed (a failing hook doesn't crash the framework)

If a hook raises an error, `HookExecutionError` is raised, the error is traced, and the framework decides (based on configuration) whether to proceed or abort the triggering operation.

### Plugin Permissions

Fine-grained access control for plugins.

| Field | Purpose |
|-------|---------|
| `plugin_id` | Which plugin |
| `permission_type` | scope_access, object_type, secret_access, hook |
| `target_ref` | What the permission applies to |

Permissions are evaluated as rules (Layer 5). If a plugin attempts an operation it hasn't been granted, `PluginPermissionError` is raised.

### Sandbox

The sandbox ensures plugins can't:
- Access objects outside their granted scopes
- Read secrets they haven't been granted refs for
- Modify the registry outside their declared kinds
- Bypass the audit trail
- Modify other plugins' data

If a plugin violates its sandbox constraints, `PluginSandboxError` is raised and the violation is traced.

## How It Connects

### To Layer 1 (Registry)
Plugins and integrations are registered constructs. Plugin-provided kinds (custom registry kinds) are registered through the registry. Hook handlers are referenced by registry URN.

### To Layer 4 (Tenancy)
Plugins have their own isolation scopes. Integration data lives within specific scopes. Cross-scope access for plugins requires explicit permission.

### To Layer 5 (Rules)
Plugin permissions are evaluated as rules. Integration access policies are rules. The rule engine doesn't know or care that the requester is a plugin — it evaluates the same way.

### To Layer 6 (Audit)
Hook executions, integration data flows, plugin lifecycle changes — all traced.

### To Layer 8 (Environments)
Environments can have integrations (e.g., a GitHub integration for a code review environment). Plugin hooks can fire on environment lifecycle events.

### To Layer 10 (Deployments)
Deployment execution often goes through integrations (the integration handles the actual push to the external system). Plugin hooks can fire pre/post deployment.

### To Layer 11 (Secrets)
Integration credentials are secrets. Plugin secret access goes through the ref system with explicit grants. The vault checks plugin permissions before resolving refs.

### To Layer 13 (Connector)
Connector templates in the marketplace are effectively plugin packages. Installing from the marketplace creates plugin instances.

## Files

```
scoped/integrations/
    __init__.py
    models.py          # Integration, Plugin, PluginHook, PluginManifest
    sandbox.py         # Plugin execution sandbox, permission enforcement
    hooks.py           # Hook registry, dispatch, execution context
    lifecycle.py       # Install, activate, suspend, uninstall plugins
    connectors.py      # Integration connection management
```

## Schema

```sql
CREATE TABLE integrations (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    integration_type TEXT NOT NULL,
    owner_id        TEXT NOT NULL REFERENCES principals(id),
    scope_id        TEXT REFERENCES scopes(id),
    config_json     TEXT NOT NULL DEFAULT '{}',
    credentials_ref TEXT,
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE plugins (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    version         TEXT NOT NULL DEFAULT '0.1.0',
    owner_id        TEXT NOT NULL REFERENCES principals(id),
    scope_id        TEXT REFERENCES scopes(id),
    manifest_json   TEXT NOT NULL DEFAULT '{}',
    state           TEXT NOT NULL DEFAULT 'installed',
    installed_at    TEXT NOT NULL,
    activated_at    TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE plugin_hooks (
    id              TEXT PRIMARY KEY,
    plugin_id       TEXT NOT NULL REFERENCES plugins(id),
    hook_point      TEXT NOT NULL,
    handler_ref     TEXT NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 0,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE plugin_permissions (
    id              TEXT PRIMARY KEY,
    plugin_id       TEXT NOT NULL REFERENCES plugins(id),
    permission_type TEXT NOT NULL,
    target_ref      TEXT NOT NULL,
    granted_at      TEXT NOT NULL,
    granted_by      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE'
);
```

## Invariants

1. Plugins run in sandboxed contexts with declared permissions only.
2. Plugin permissions are reviewed and explicitly granted at install time.
3. Hook executions are traced and sandboxed — a failing hook doesn't crash the framework.
4. Integration credentials are managed as secrets (never in config).
5. Plugin data is isolated in the plugin's own scope.
6. All plugin lifecycle transitions are traced.
