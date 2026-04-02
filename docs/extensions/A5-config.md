# A5: Configuration Hierarchy

**Extends:** Layer 4 (Tenancy)

## Purpose

Applications need per-scope configuration that inherits down the scope hierarchy. A setting defined at the organization scope should cascade to all team scopes unless explicitly overridden. This extension adds hierarchical key-value settings bound to scopes.

## Core Concepts

### ScopedSetting

A key-value configuration entry bound to a scope.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `scope_id` | Which scope this setting belongs to |
| `key` | Setting name (e.g., `max_upload_size`, `theme`) |
| `value` | JSON-serialized value (string, number, object, array) |
| `created_at` | When the setting was created |
| `created_by` | Which principal set it |

### Inheritance

Settings inherit down the scope hierarchy:
1. Check the current scope for the key
2. If not found, walk up to the parent scope
3. Continue until found or root scope is reached
4. If no scope in the chain has the setting, return the default (or None)

This means a setting at the org level is automatically available to all child scopes unless a child explicitly overrides it.

### ConfigStore

Service layer for managing settings:

- `set_setting(scope_id, key, value, principal_id)` — create or update
- `get_setting(scope_id, key)` — direct lookup (no inheritance)
- `resolve_setting(scope_id, key, default)` — walk hierarchy
- `resolve_all(scope_id)` — resolve all settings with inheritance
- `list_settings(scope_id)` — list direct settings only
- `delete_setting(scope_id, key, principal_id)` — remove a setting
- `get_effective_settings(scope_id)` — merged dict of all resolved settings

### Access Control

- Only scope owners can set or delete settings
- Frozen scopes cannot have settings modified (`ScopeFrozenError`)
- Setting resolution respects scope visibility — you can only resolve settings for scopes you can see

## Schema

```sql
CREATE TABLE scope_settings (
    id              TEXT PRIMARY KEY,
    scope_id        TEXT NOT NULL REFERENCES scopes(id),
    key             TEXT NOT NULL,
    value_json      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    UNIQUE(scope_id, key)
);
```

## Files

```
scoped/tenancy/
    config.py          # ScopedSetting, ConfigStore
```

## Usage

```python
from scoped.tenancy.config import ConfigStore

config = ConfigStore(backend)

# Set at org level
config.set_setting(org_scope_id, "max_upload_mb", 100, principal_id=admin_id)

# Override at team level
config.set_setting(team_scope_id, "max_upload_mb", 50, principal_id=team_lead_id)

# Resolve — team scope gets 50 (overridden), theme gets org default
upload_limit = config.resolve_setting(team_scope_id, "max_upload_mb")  # 50
theme = config.resolve_setting(team_scope_id, "theme", default="light")  # "light"
```

## Resolution Chain

`ConfigResolver.resolve()` returns a `ResolvedSetting` with a `resolution_chain`
field that shows every ancestor value encountered during hierarchy traversal,
ordered root-to-leaf:

```python
from scoped.tenancy.config import ConfigResolver

resolver = ConfigResolver(backend)
result = resolver.resolve(team_scope_id, "max_upload_mb")

result.value              # 50 (closest to queried scope wins)
result.source_scope_id    # team_scope_id
result.inherited          # False (set directly on this scope)
result.resolution_chain   # [(org_scope_id, 100), (team_scope_id, 50)]
```

This enables UIs and debugging tools to show exactly where a setting comes
from and what values it overrides at each level of the hierarchy.

`resolve_all()` also populates `resolution_chain` for every key.

## Invariants

1. Inheritance walks up the scope hierarchy — child overrides take precedence.
2. Only scope owners can modify settings.
3. Frozen scopes reject all setting modifications.
4. Settings are JSON-serialized — any JSON value is valid.
