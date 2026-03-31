# A7: General Templates

**Extends:** Layer 1 (Registry)

## Purpose

Environment templates exist, but the pattern should generalize. Any construct type — scopes, pipelines, rule sets, objects — can benefit from reusable blueprints. A template defines a default schema (JSON dict) that can be instantiated with overrides via deep merge.

## Core Concepts

### Template

A reusable blueprint for any construct type.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `name` | Human-readable label |
| `description` | What this template creates |
| `template_type` | What kind of construct: `scope`, `environment`, `pipeline`, `rule_set`, etc. |
| `owner_id` | The principal who created it |
| `schema` | JSON dict — the default values for instantiation |
| `current_version` | Latest version number |
| `scope_id` | Optional — restricts template visibility to a scope |
| `lifecycle` | ACTIVE, ARCHIVED |

### TemplateVersion

Templates are versioned. Every update to the schema creates a new `TemplateVersion`, enabling evolution tracking and instantiation from specific versions.

### Instantiation

`TemplateStore.instantiate(template_id, overrides, version)` produces an `InstantiationResult`:

1. Load the template's schema (or a specific version's schema)
2. Deep-merge the overrides on top of the defaults
3. Return the merged data without creating any objects (the caller decides what to do with it)

**Deep merge rules:**
- Dict + Dict → recursive merge (base keys preserved, override keys added/replaced)
- Any other type → override replaces base
- All values are deep-copied to prevent mutation

### Access Control

- Only the template owner can update or archive a template
- Archived templates cannot be updated or instantiated
- Templates with a `scope_id` are filtered by scope in list queries

## Schema

```sql
CREATE TABLE templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    template_type   TEXT NOT NULL,
    owner_id        TEXT NOT NULL REFERENCES principals(id),
    schema_json     TEXT NOT NULL DEFAULT '{}',
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    scope_id        TEXT REFERENCES scopes(id),
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE template_versions (
    id              TEXT PRIMARY KEY,
    template_id     TEXT NOT NULL REFERENCES templates(id),
    version         INTEGER NOT NULL,
    schema_json     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    change_reason   TEXT NOT NULL DEFAULT '',
    UNIQUE(template_id, version)
);
```

## Files

```
scoped/registry/
    templates.py       # Template, TemplateVersion, TemplateStore, InstantiationResult
```

## Usage

```python
from scoped.registry.templates import TemplateStore

store = TemplateStore(backend)

# Create a scope template
template = store.create_template(
    name="Team Scope",
    template_type="scope",
    owner_id=admin_id,
    schema={
        "settings": {"max_members": 50, "default_role": "editor"},
        "rules": {"allow_external_sharing": False},
    },
)

# Instantiate with overrides
result = store.instantiate(
    template.id,
    overrides={"settings": {"max_members": 100}},
    principal_id=admin_id,
)
# result.data = {"settings": {"max_members": 100, "default_role": "editor"},
#                "rules": {"allow_external_sharing": False}}
```

## Invariants

1. Templates are versioned — updates create new versions, old versions are retained.
2. Instantiation never mutates the template — always returns a deep-copied merge.
3. Archived templates cannot be updated or instantiated.
4. Only the template owner can modify a template.
5. Deep merge is recursive for dicts; scalars/lists are replaced wholesale.
