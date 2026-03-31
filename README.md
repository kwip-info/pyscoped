# Scoped

**Universal object-isolation and tenancy framework for Python.**

Scoped guarantees that anything built on it can be isolated, shared, traced, and rolled back — to any degree, at any time, by anyone with the right to do so.

Zero dependencies. SQLite included. 1,500+ tests. Python 3.11+.

```bash
pip install pyscoped
```

## Why Scoped

Most frameworks give you a database and leave isolation, sharing, auditing, and rollback as your problem. Scoped makes them structural guarantees:

- **Every object is creator-private by default.** Sharing requires explicit projection into a scope.
- **Every mutation creates a new version.** No in-place updates. Full history preserved.
- **Every action is hash-chained.** Tamper-evident audit trail with before/after state.
- **Everything is rollbackable.** Any action can be reversed to any point in time.
- **Deny always wins.** When rules conflict, DENY overrides ALLOW. No exceptions.

This makes Scoped the base layer for multi-tenant applications, clinical systems, financial platforms, compliance-sensitive workflows, and AI agent orchestration.

## Quick Start

```python
from scoped.storage.sqlite import SQLiteBackend
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.tenancy.projection import ProjectionManager
from scoped.audit.writer import AuditWriter

# 1. Initialize storage (SQLite, zero config)
backend = SQLiteBackend("app.db")
backend.initialize()

# 2. Create services
principals = PrincipalStore(backend)
audit = AuditWriter(backend)
manager = ScopedManager(backend, audit_writer=audit)
scopes = ScopeLifecycle(backend, audit_writer=audit)
projections = ProjectionManager(backend, audit_writer=audit)

# 3. Create a principal (the acting user)
alice = principals.create_principal(kind="user", display_name="Alice")
bob = principals.create_principal(kind="user", display_name="Bob")

# 4. Create an object — it's creator-private
obj, v1 = manager.create(
    object_type="document",
    owner_id=alice.id,
    data={"title": "Q4 Report", "status": "draft"},
)

# Alice can read it
assert manager.get(obj.id, principal_id=alice.id) is not None

# Bob cannot
assert manager.get(obj.id, principal_id=bob.id) is None

# 5. Update creates a new immutable version
obj, v2 = manager.update(
    obj.id,
    principal_id=alice.id,
    data={"title": "Q4 Report", "status": "final"},
    change_reason="Finalized for review",
)
assert obj.current_version == 2  # v1 is preserved, untouched

# 6. Share via scope projection
team = scopes.create_scope(name="Finance Team", owner_id=alice.id)
from scoped.tenancy.models import ScopeRole
scopes.add_member(team.id, principal_id=bob.id, role=ScopeRole.VIEWER, granted_by=alice.id)
projections.project(scope_id=team.id, object_id=obj.id, projected_by=alice.id)

# Now Bob can see it
assert manager.get(obj.id, principal_id=bob.id) is None  # manager is owner-only
# But visibility engine shows it
from scoped.tenancy.engine import VisibilityEngine
vis = VisibilityEngine(backend)
assert obj.id in vis.visible_object_ids(bob.id)

# 7. Every action was traced with hash-chained audit
from scoped.audit.query import AuditQuery
trail = AuditQuery(backend).for_target("document", obj.id)
assert len(trail) >= 2  # CREATE + UPDATE, each with before/after state
```

## Architecture

16 composable layers. Each depends only on layers below it.

```
Layer 0   Compliance    Validates all invariants across layers
Layer 1   Registry      Universal construct registration (URNs)
Layer 2   Identity      Generic principal machinery + ScopedContext
Layer 3   Objects       Versioned, isolated data objects
Layer 4   Tenancy       Scopes, membership, projection (sharing)
Layer 5   Rules         Deny-overrides policy engine
Layer 6   Audit         Hash-chained, immutable, append-only trace
Layer 7   Temporal      Point-in-time reconstruction + rollback
Layer 8   Environments  Ephemeral workspaces
Layer 9   Flow          Stages, pipelines, promotions
Layer 10  Deployments   Graduation to external targets with gates
Layer 11  Secrets       Encrypted vault with zero-trust access
Layer 12  Integrations  Sandboxed plugins, hooks, external systems
Layer 13  Connector     Cross-org meshing, federation, marketplace
Layer 14  Events        Asynchronous scoped event bus + webhooks
Layer 15  Notifications Principal-targeted messages
Layer 16  Scheduling    Recurring schedules, scoped job execution
```

9 extensions enrich existing layers: Migrations (A1), Contracts (A2), Rule Extensions (A3), Blobs (A4), Config Hierarchy (A5), Search (A6), Templates (A7), Tiering (A8), Import/Export (A9).

## The 10 Invariants

These are absolute. The compliance engine (Layer 0) validates them.

1. **Nothing exists without registration.** Every construct has a URN in the registry.
2. **Nothing happens without identity.** Every operation requires an acting principal.
3. **Nothing is shared by default.** Objects start creator-private. Sharing is explicit.
4. **Nothing happens without a trace.** Every action produces a hash-chained audit entry.
5. **Nothing is truly deleted.** Objects are tombstoned. Versions retained. Audit is append-only.
6. **Deny always wins.** DENY overrides ALLOW when rules conflict.
7. **Revocation is immediate.** Same-transaction enforcement.
8. **Everything is versioned.** Every mutation creates a new immutable version.
9. **Everything is rollbackable.** Any action can be reversed to any point in time.
10. **Secrets never leak.** Values never appear in audit, snapshots, or connector traffic.

## Core API

### Objects (Layer 3)

```python
from scoped.objects.manager import ScopedManager

manager = ScopedManager(backend, audit_writer=audit)

# Create — returns (ScopedObject, ObjectVersion)
obj, ver = manager.create(object_type="task", owner_id=user.id, data={"title": "Ship it"})

# Read — returns None if principal cannot access
obj = manager.get(obj.id, principal_id=user.id)

# Update — creates new version, never modifies old
obj, ver = manager.update(obj.id, principal_id=user.id, data={"title": "Ship it", "done": True})

# Soft delete — tombstones, never physically deletes
tombstone = manager.tombstone(obj.id, principal_id=user.id, reason="Obsolete")
```

### Tenancy (Layer 4)

```python
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.tenancy.projection import ProjectionManager
from scoped.tenancy.models import ScopeRole, AccessLevel

scopes = ScopeLifecycle(backend, audit_writer=audit)
projections = ProjectionManager(backend, audit_writer=audit)

# Create scope (owner auto-added as OWNER member)
scope = scopes.create_scope(name="Team Alpha", owner_id=alice.id)

# Add members
scopes.add_member(scope.id, principal_id=bob.id, role=ScopeRole.EDITOR, granted_by=alice.id)

# Project an object into the scope (sharing it with members)
projections.project(scope_id=scope.id, object_id=obj.id, projected_by=alice.id)

# Revoke sharing
projections.revoke_projection(scope_id=scope.id, object_id=obj.id, revoked_by=alice.id)
```

### Rules (Layer 5)

```python
from scoped.rules.engine import RuleStore, RuleEngine
from scoped.rules.models import RuleType, RuleEffect, BindingTargetType

store = RuleStore(backend)
rule = store.create_rule(
    name="deny-external-access",
    rule_type=RuleType.ACCESS,
    effect=RuleEffect.DENY,
    priority=10,
    created_by=admin.id,
)
store.bind_rule(rule.id, target_type=BindingTargetType.SCOPE, target_id=scope.id, bound_by=admin.id)

engine = RuleEngine(backend)
result = engine.evaluate(action="read", principal_id=user.id, scope_id=scope.id)
# result.allowed, result.deny_rules, result.allow_rules
```

### Audit (Layer 6)

```python
from scoped.audit.writer import AuditWriter
from scoped.audit.query import AuditQuery

writer = AuditWriter(backend)
entry = writer.record(
    actor_id=user.id,
    action=ActionType.CREATE,
    target_type="document",
    target_id=obj.id,
    after_state={"title": "Draft"},
)
# entry.hash — SHA-256 hash linking to previous entry
# entry.previous_hash — hash of the entry before this one
# entry.sequence — monotonically increasing sequence number

query = AuditQuery(backend)
trail = query.for_target("document", obj.id)  # full history
trail = query.for_actor(user.id)               # everything a user did
```

### Temporal (Layer 7)

```python
from scoped.temporal.rollback import RollbackExecutor
from scoped.temporal.reconstruction import StateReconstructor

# Roll back a single action
executor = RollbackExecutor(backend, audit_writer=audit)
result = executor.rollback_action(trace_id, actor_id=admin.id, reason="Mistake")

# Roll back to a point in time
result = executor.rollback_to_timestamp("document", doc.id, at=yesterday, actor_id=admin.id)

# Cascading rollback (action + all dependent actions)
result = executor.rollback_cascade(trace_id, actor_id=admin.id)

# Reconstruct state at a past timestamp
reconstructor = StateReconstructor(backend)
state = reconstructor.at_timestamp("document", doc.id, timestamp)
```

## Framework Adapters

Install with extras for your framework:

```bash
pip install pyscoped[django]    # Django ORM backend + middleware
pip install pyscoped[fastapi]   # FastAPI middleware + Pydantic schemas
pip install pyscoped[flask]     # Flask extension + admin blueprint
pip install pyscoped[mcp]       # MCP server for AI agents
```

### Django

```python
# settings.py
INSTALLED_APPS = ["scoped.contrib.django"]
MIDDLEWARE = ["scoped.contrib.django.middleware.ScopedContextMiddleware"]

# Uses the Django database connection as the storage backend.
# Management commands: scoped_health, scoped_audit, scoped_compliance
```

### FastAPI

```python
from fastapi import FastAPI
from scoped.contrib.fastapi.middleware import ScopedContextMiddleware
from scoped.contrib.fastapi.router import router as scoped_router

app = FastAPI()
app.add_middleware(ScopedContextMiddleware, backend=backend)
app.include_router(scoped_router)  # /scoped/health, /scoped/audit
```

### Flask

```python
from flask import Flask
from scoped.contrib.flask.extension import ScopedExtension

app = Flask(__name__)
scoped = ScopedExtension(app)  # auto-inits backend, injects g.scoped_context
```

### MCP (Model Context Protocol)

```python
from scoped.contrib.mcp.server import create_scoped_server

mcp = create_scoped_server(backend)
mcp.run()
# Tools: create_principal, create_object, get_object, create_scope, list_audit, health_check
# Resources: scoped://principals, scoped://health, scoped://audit/recent
```

## Storage

The default backend is SQLite (zero dependencies, included). The `StorageBackend` interface can be implemented for any database:

```python
from scoped.storage.sqlite import SQLiteBackend

# In-memory (tests, prototyping)
backend = SQLiteBackend(":memory:")
backend.initialize()

# File-based (production single-node)
backend = SQLiteBackend("app.db")
backend.initialize()

# Django ORM backend (production)
from scoped.contrib.django import get_backend
backend = get_backend()
```

## Testing

Scoped includes a compliance testing engine for verifying invariants:

```python
from scoped.testing.base import ScopedTestCase

class MyTest(ScopedTestCase):
    def test_isolation(self):
        obj = self.create_object("doc", self.user_a, {"title": "Private"})
        # user_b cannot read user_a's object
        assert self.read_object(obj.id, as_principal=self.user_b) is None

    def test_versioning(self):
        obj = self.create_object("doc", self.user_a, {"v": 1})
        self.manager.update(obj.id, principal_id=self.user_a.id, data={"v": 2})
        # Both versions exist
        versions = self.backend.fetch_all(
            "SELECT * FROM object_versions WHERE object_id = ?", (obj.id,)
        )
        assert len(versions) == 2
```

```bash
pip install pyscoped[dev]
pytest                          # 1,500+ tests
pytest tests/test_objects/      # one layer
pytest tests/test_compliance/   # invariant validation
```

## Test Coverage

| Component | Tests |
|-----------|-------|
| Core Layers 1-13 | 820 |
| Extensions A1-A9 | 386 |
| Events, Notifications, Scheduling | 117 |
| Compliance Engine (Layer 0) | 87 |
| Framework Adapters (D1-D4) | 83 |
| **Total** | **1,493+** |

## Documentation

- [Architecture Overview](https://github.com/kwip-info/pyscoped/blob/main/docs/architecture.md)
- [Getting Started Guide](https://github.com/kwip-info/pyscoped/blob/main/docs/getting-started.md)
- [API Reference](https://github.com/kwip-info/pyscoped/blob/main/docs/api-reference.md)
- [Framework Adapters](https://github.com/kwip-info/pyscoped/blob/main/docs/adapters.md)
- [Layer Documentation](https://github.com/kwip-info/pyscoped/tree/main/docs/layers) (Layers 0-16)
- [Extension Documentation](https://github.com/kwip-info/pyscoped/tree/main/docs/extensions) (A1-A9)
- [Changelog](https://github.com/kwip-info/pyscoped/blob/main/CHANGELOG.md)

## Requirements

- Python 3.11+
- No required dependencies (SQLite backend included)

## License

MIT License. See [LICENSE](https://github.com/kwip-info/pyscoped/blob/main/LICENSE) for details.
