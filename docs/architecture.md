# Scoped Architecture

## What Scoped Is

Scoped is a framework that makes one guarantee: **anything built on it can be isolated, shared, traced, and rolled back to any degree, at any time, by anyone with the right to do so.**

Today, a single person can spin up an AI conversation, do a throwaway task, and move on. That works for individuals. It does not work for organizations — teams that need to collaborate on throwaway work, share results selectively, enforce who sees what, and maintain a complete audit trail of everything that happened.

Scoped makes the throwaway-task pattern work at organizational scale. It is the base layer that any application can be built on to inherit strict isolation, tenancy, traceability, and flow control by default — not as an afterthought.

## The River

Information in Scoped flows like a river.

Every piece of data starts as a **drop** — created by a single user, visible to no one else. That drop exists in an **environment** — an ephemeral workspace, a throwaway context where work happens.

When the user decides to share, they create a **scope** — a channel that other principals can see into. The drop is **projected** into the scope, and now it flows in a **stream** visible to the scope's members.

Streams from different scopes can feed into **pipelines** — sequences of **stages** (draft, review, approved, deployed) that govern how work matures. Objects flow through stages via **flow channels** — explicit, directional pipes that connect environments to scopes, scopes to scopes, stages to stages.

Teams within an organization share a **river** — the aggregate of their scopes, environments, and flows. The organization itself is a **watershed** — the complete Scoped instance governing all rivers within it.

When two organizations want to collaborate, they build a **canal** — a **connector** that bridges two watersheds under mutual agreement, governed by policies on both sides. The **marketplace** is the map that shows where canals can be built — a public discovery layer for connector templates, plugins, and integrations.

At every point in this system — from the initial drop to a cross-organization canal — **every action is traced**, **every object is versioned**, **every permission is explicit**, and **everything can be rolled back**.

## How Everything Connects

```
┌─────────────────────────────────────────────────────────────────────┐
│                        COMPLIANCE ENGINE (Layer 0)                  │
│          Validates every invariant across all layers below          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                  PLATFORM CONNECTOR (Layer 13)               │   │
│  │         Cross-org meshing, federation, marketplace           │   │
│  │  ┌────────────────────────────────────────────────────────┐  │   │
│  │  │              INTEGRATIONS & PLUGINS (Layer 12)         │  │   │
│  │  │       Sandboxed extensions, hooks, external systems    │  │   │
│  │  │  ┌──────────────────────────────────────────────────┐  │  │   │
│  │  │  │                  SECRETS (Layer 11)              │  │  │   │
│  │  │  │       Encrypted vault, refs, zero-trust access   │  │  │   │
│  │  │  ├──────────────────────────────────────────────────┤  │  │   │
│  │  │  │               DEPLOYMENTS (Layer 10)             │  │  │   │
│  │  │  │      Graduation to external targets, gates       │  │  │   │
│  │  │  ├──────────────────────────────────────────────────┤  │  │   │
│  │  │  │              STAGES & FLOW (Layer 9)             │  │  │   │
│  │  │  │    Pipelines, channels, promotions — the river   │  │  │   │
│  │  │  ├──────────────────────────────────────────────────┤  │  │   │
│  │  │  │              ENVIRONMENTS (Layer 8)              │  │  │   │
│  │  │  │   Ephemeral workspaces — the unit of work        │  │  │   │
│  │  │  └──────────────┬───────────────────────────────────┘  │  │   │
│  │  │                 │                                      │  │   │
│  │  │       ┌─────────┴─────────┐                            │  │   │
│  │  │       │ TEMPORAL (Layer 7)│  Point-in-time rollback    │  │   │
│  │  │       └─────────┬─────────┘                            │  │   │
│  │  │       ┌─────────┴─────────┐                            │  │   │
│  │  │       │  AUDIT (Layer 6)  │  Hash-chained trace of ALL │  │   │
│  │  │       └─────────┬─────────┘                            │  │   │
│  │  │       ┌─────────┴─────────┐                            │  │   │
│  │  │       │  RULES (Layer 5)  │  Deny-overrides policy     │  │   │
│  │  │       └─────────┬─────────┘                            │  │   │
│  │  │       ┌─────────┴─────────┐                            │  │   │
│  │  │       │ TENANCY (Layer 4) │  Scopes, projections       │  │   │
│  │  │       └─────────┬─────────┘                            │  │   │
│  │  │       ┌─────────┴─────────┐                            │  │   │
│  │  │       │ OBJECTS (Layer 3) │  Versioned, isolated       │  │   │
│  │  │       └─────────┬─────────┘                            │  │   │
│  │  │       ┌─────────┴─────────┐                            │  │   │
│  │  │       │IDENTITY (Layer 2) │  Generic principals        │  │   │
│  │  │       └─────────┬─────────┘                            │  │   │
│  │  │       ┌─────────┴─────────┐                            │  │   │
│  │  │       │REGISTRY (Layer 1) │  Everything registered     │  │   │
│  │  │       └───────────────────┘                            │  │   │
│  │  └────────────────────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────┘   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    STORAGE BACKEND                           │   │
│  │            SQLite │ Django ORM │ Postgres │ ...              │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## The Dependency Chain

Every layer depends on the layers below it. Nothing is optional — if you use Scoped, you get all of it.

**Layer 1 (Registry)** is the foundation. Every construct — a model, a function, a class, a secret, a connector — must be registered to exist. The registry assigns a URN, tracks lifecycle, and makes things discoverable. Without the registry, nothing else can reference anything.

**Layer 2 (Identity)** sits on the registry. Principals are registered constructs. Every operation requires a principal — there is no anonymous action in Scoped. Identity is generic: the framework provides the machinery (context, relationships, resolution), not the concrete types.

**Layer 3 (Objects)** sits on identity and registry. Every data object is a registered construct owned by a principal. Every mutation creates a new version. Nothing is deleted — only tombstoned. Default visibility: creator only.

**Layer 4 (Tenancy)** sits on objects and identity. Scopes are the sharing primitive. To share an object, the owner creates a scope, adds members, and projects the object in. Scopes can nest. Scopes themselves are registered, versioned, audited.

**Layer 5 (Rules)** sits on everything above. Rules modify what the scoping engine allows. They attach to scopes, principals, object types, or specific objects. Deny-overrides: any DENY wins. Rules are versioned — changing a rule is a traced, rollbackable action.

**Layer 6 (Audit)** wraps everything. Every action through every layer produces an immutable, hash-chained trace entry. The audit trail is the backbone of compliance — if it didn't produce a trace, it didn't happen. Audit visibility is itself governed by rules.

**Layer 7 (Temporal)** sits on audit. Because every action is traced and every object is versioned, any state can be reconstructed at any point in time. Rollbacks are themselves traced actions. Cascading rollbacks follow the dependency chain.

**Layer 8 (Environments)** sits on tenancy and objects. Environments are the collaborative workspace — the ephemeral context where throwaway work happens. Each environment gets its own isolation scope. Everything inside is invisible outside unless explicitly projected.

**Layer 9 (Flow)** sits on environments and tenancy. Flow defines how information moves — stages in pipelines, channels between environments and scopes, promotions from ephemeral to persistent. The river.

**Layer 10 (Deployments)** sits on flow. Deployments are the final flow — from the Scoped world into an external target. Gate checks enforce that all stages passed, all rules satisfied, all approvals collected before graduation.

**Layer 11 (Secrets)** is a cross-cutting concern that touches objects, tenancy, audit, and environments. Secrets are the highest-security objects — encrypted at rest, accessed via refs that are scope-checked on every dereference, never serialized in plaintext, never included in environment snapshots or audit state.

**Layer 12 (Integrations & Plugins)** sits on secrets, registry, and rules. External systems connect through registered integrations with scoped credentials (secret refs). Plugins extend the framework through sandboxed hooks with declared permissions. Both are first-class scoped citizens.

**Layer 13 (Connector & Marketplace)** sits on everything. Connectors bridge two separate Scoped instances under mutual agreement. Marketplace provides public discovery. This is where the river metaphor reaches its full expression — canals between watersheds, a map for the world.

**Layer 0 (Compliance)** wraps everything from the outside. It validates every invariant across every layer — at test time (static analysis) and at runtime (middleware enforcement). If a construct isn't registered, if an action doesn't produce a trace, if an object leaks outside its scope — compliance catches it.

## The Invariants

These are absolute. The compliance engine enforces every one of them.

1. **Nothing exists without registration.** Every construct — data, code, behavioral — must have a registry entry with a URN and lifecycle state.

2. **Nothing happens without identity.** Every operation must have a `ScopedContext` identifying the acting principal. No anonymous actions.

3. **Nothing is shared by default.** Every object starts creator-private. Sharing requires explicit scope creation, membership grants, and object projection.

4. **Nothing happens without a trace.** Every action — including reads, access checks, and rule evaluations — produces an immutable, hash-chained audit entry.

5. **Nothing is truly deleted.** Objects are tombstoned, not removed. Versions are retained. Audit entries are append-only.

6. **Deny always wins.** When rules conflict, DENY overrides ALLOW. Security is the default; access must be explicitly granted.

7. **Revocation is immediate.** When access is revoked, it takes effect within the same transaction. No eventual consistency for security.

8. **Everything is versioned.** Objects, rules, scopes, secrets — every mutation creates a new version. Old versions are retained for audit and rollback.

9. **Everything is rollbackable.** Any action can be reversed to any point in time. Rollbacks are themselves traced and rule-governed.

10. **Secrets never leak.** Secret values never appear in audit trails, environment snapshots, or connector traffic. Access is via refs that are scope-checked on every dereference.

## Phase A: Extensions to Existing Layers

After the 13 core layers were completed, the framework was expanded with 9 extensions that enrich existing layers with commonly needed capabilities. Each extension adds to an existing layer without changing the layer numbering.

| Extension | Name | Document | Extends | Purpose |
|-----------|------|----------|---------|---------|
| A1 | Schema Migrations | [A1-migrations.md](extensions/A1-migrations.md) | Storage | Versioned schema evolution with up/down migrations |
| A2 | Contracts & Validation | [A2-contracts.md](extensions/A2-contracts.md) | L1 Registry + L3 Objects | Declared object schemas, field types, cross-field validation |
| A3 | Rule Extensions | [A3-rule-extensions.md](extensions/A3-rule-extensions.md) | L5 Rules | Redaction, rate limiting, quotas, feature flags |
| A4 | Blob / Media Storage | [A4-blobs.md](extensions/A4-blobs.md) | L3 Objects + Storage | Binary content with versioning, isolation, and audit |
| A5 | Configuration Hierarchy | [A5-config.md](extensions/A5-config.md) | L4 Tenancy | Per-scope settings with hierarchical inheritance |
| A6 | Search / Indexing | [A6-search.md](extensions/A6-search.md) | L3 Objects | Scope-aware full-text search over object data |
| A7 | General Templates | [A7-templates.md](extensions/A7-templates.md) | L1 Registry | Reusable blueprints for any construct type |
| A8 | Storage Tiering / Archival | [A8-tiering.md](extensions/A8-tiering.md) | Storage | Hot/warm/cold/glacial tiers, retention policies, sealed archives |
| A9 | Data Import / Export | [A9-import-export.md](extensions/A9-import-export.md) | L3 Objects | Portable object packages with ID remapping and integrity verification |

## Phase B: New Layers

| Layer | Name | Purpose |
|-------|------|---------|
| 14 | Events & Webhooks | Asynchronous, scoped event bus with outbound/inbound webhook delivery |
| 15 | Notifications | Principal-targeted messages generated from events and rules |
| 16 | Scheduling & Jobs | Time-based actions, recurring schedules, scoped job execution |

## Phase C: Compliance Testing Engine

Layer 0 — wraps all other layers. Validates every invariant at test time (static) and runtime.

| Module | Purpose |
|--------|---------|
| `base.py` | ScopedTestCase — base test class with isolation helpers and assertion methods |
| `introspection.py` | RegistryIntrospector — scan registry for completeness, orphans, duplicates |
| `auditor.py` | ComplianceAuditor — 6 static checks (registry, trace, isolation, rules, scopes, secrets) |
| `middleware.py` | ComplianceMiddleware — runtime enforcement (context, trace, version, revocation, secrets) |
| `fuzzer.py` | IsolationFuzzer — randomized access pattern testing with seed-based determinism |
| `rollback.py` | RollbackVerifier — verify create/update/tombstone rollback traces are correct |
| `health.py` | HealthChecker — DB connectivity, schema tables, audit chain, migration state |
| `reports.py` | ComplianceReporter — generate full reports combining audit, introspection, and health |

## Roadmap

### Phase D: Framework Adapters

All adapters live under `scoped.contrib` and are installed via extras (`pip install scoped[django]`, etc.).

| Adapter | Module | Framework | Purpose |
|---------|--------|-----------|---------|
| D1 | `scoped.contrib.django` | Django 4.2+ | DjangoORMBackend, middleware, AppConfig, management commands |
| D2 | `scoped.contrib.fastapi` | FastAPI 0.100+ | Starlette middleware, dependencies, Pydantic schemas, admin router |
| D3 | `scoped.contrib.flask` | Flask 3.0+ | Extension with init_app, before/after hooks, admin blueprint |
| D4 | `scoped.contrib.mcp` | MCP SDK 1.0+ | FastMCP server with tools and resources for AI agents |

## Layer Index

| Layer | Name | Document | Purpose |
|-------|------|----------|---------|
| 0 | Compliance | [00-compliance.md](layers/00-compliance.md) | Testing engine that enforces all invariants |
| 1 | Registry | [01-registry.md](layers/01-registry.md) | Universal construct registration |
| 2 | Identity | [02-identity.md](layers/02-identity.md) | Generic principal machinery |
| 3 | Objects | [03-objects.md](layers/03-objects.md) | Versioned, isolated data objects |
| 4 | Tenancy | [04-tenancy.md](layers/04-tenancy.md) | Scoping, membership, projection |
| 5 | Rules | [05-rules.md](layers/05-rules.md) | Deny-overrides policy engine |
| 6 | Audit | [06-audit.md](layers/06-audit.md) | Hash-chained immutable trace |
| 7 | Temporal | [07-temporal.md](layers/07-temporal.md) | Point-in-time rollback |
| 8 | Environments | [08-environments.md](layers/08-environments.md) | Ephemeral & persistent workspaces |
| 9 | Flow | [09-flow.md](layers/09-flow.md) | Stages, pipelines, promotions |
| 10 | Deployments | [10-deployments.md](layers/10-deployments.md) | Graduation to external targets |
| 11 | Secrets | [11-secrets.md](layers/11-secrets.md) | Encrypted vault & zero-trust access |
| 12 | Integrations | [12-integrations.md](layers/12-integrations.md) | Plugins, hooks, external systems |
| 13 | Connector | [13-connector.md](layers/13-connector.md) | Cross-org meshing & marketplace |
| 14 | Events | [14-events.md](layers/14-events.md) | Asynchronous scoped event bus & webhooks |
| 15 | Notifications | [15-notifications.md](layers/15-notifications.md) | Principal-targeted messages from events & rules |
| 16 | Scheduling | [16-scheduling.md](layers/16-scheduling.md) | Recurring schedules & scoped job execution |

## Test Coverage

| Component | Tests |
|-----------|-------|
| Layers 1–13 (core) | 820 |
| A1: Schema Migrations | 38 |
| A2: Contracts & Validation | 40 |
| A3: Rule Extensions | 71 |
| A4: Blob / Media Storage | 30 |
| A5: Configuration Hierarchy | 34 |
| A6: Search / Indexing | 26 |
| A7: General Templates | 40 |
| A8: Storage Tiering / Archival | 47 |
| A9: Data Import / Export | 26 |
| B1: Events & Webhooks | 61 |
| B2: Notifications | 29 |
| B3: Scheduling & Jobs | 27 |
| C1: Compliance Engine | 87 |
| D1–D4: Framework Adapters | 83 |
| **Total** | **1493** (all passing) |
