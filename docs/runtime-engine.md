# Runtime Engine — Future Architecture

> This document captures the design direction for the Scoped Runtime Engine:
> the serving layer that turns a canvas-built application into a live,
> client-facing product with auto-generated UI, self-documenting API, and
> system-wide metrics.

---

## Overview

The Scoped platform has three distinct operational surfaces:

| Surface | User | Purpose |
|---------|------|---------|
| **System** (`/system/`) | System operator | Design-time: build the application declaratively |
| **Runtime Engine** (`/`) | End-user clients | Serve-time: interact with the built application |
| **Metrics Dashboard** | System operator / billing | Observe-time: usage, heads, system health |

The Runtime Engine reads the registry and manifest at runtime — it does not
generate static code. Changes made on the canvas propagate to the live
application immediately.

---

## Stage 1: Deploy

Deploy the orchestrator as an isolated Scoped instance. Each deployment gets:

- Its own SQLite/Postgres database
- Its own registry (URN namespace)
- Its own audit chain (hash-chained, tamper-evident)
- Its own identity graph

The operator who deploys and builds the application operates as `system` — not
a principal in the identity layer, but the entity that defines the system
itself. This is a distinct privilege level above any principal role.

---

## Stage 2: Build via Canvas

The canvas is the design surface. The operator declares:

- **Object types** with field schemas (via construct metadata)
- **Principal kinds** (user types, bot types, service accounts)
- **Scopes** (workspaces, teams, projects — the sharing boundaries)
- **Rules** (access policies, rate limits, feature flags, redaction)
- **Flows** (pipelines, approval stages, promotion paths)
- **Schedules** (recurring jobs, maintenance windows)
- **Events & Notifications** (triggers, webhook endpoints, alert rules)

Everything placed on the canvas is registered in the universal registry with
a URN, typed metadata, and lifecycle state. The canvas manifest is a
declarative snapshot of the entire application schema.

---

## Stage 3: Auto-Generated Client UI

The Runtime Engine generates the client-facing UI dynamically from the
registry. This is **option A: runtime adaptation** — the UI reads the current
registry state on every request and renders accordingly. No code generation
step, no regeneration on changes.

### How It Works

The registry contains everything needed to render a UI:

| Registry Data | UI Output |
|---------------|-----------|
| Object types + field metadata | CRUD forms, list views, detail pages |
| Scopes + membership | Workspace switcher, team views |
| Rules + effects | Visible/hidden fields, disabled actions, access gates |
| Principals + kinds | Login, role assignment, profile pages |
| Flows + stages | Progress indicators, approval UIs, kanban boards |
| Notifications + rules | Notification center, alert preferences |

### Scoped by Identity

The client UI is scoped to the logged-in principal. When Alice (role: editor
in scope "Team Alpha") opens the app:

- She sees objects projected into her scopes
- Filtered by rules that apply to her principal kind and role
- Actions available to her are determined by the rules engine
- Every interaction is traced in the audit chain
- She never sees the canvas, registry internals, or system constructs

### System vs Client Boundary

| Concern | System (`/system/`) | Client (Runtime Engine `/`) |
|---------|-----------------|---------------------|
| Define object types | Yes | No |
| Create instances of objects | Yes | Yes (if rules allow) |
| Configure rules | Yes | No |
| See audit trail | Full | Own actions only (rule-governed) |
| Manage scopes | Yes | Limited (if scope admin) |
| See registry | Full | Never |

---

## Stage 4: Self-Documenting API

The API generates its own documentation from the registry. Every registered
construct with typed metadata produces API surface:

### Route Generation

```
GET    /api/v1/{object_type}/           List (scope-filtered)
GET    /api/v1/{object_type}/{id}/      Detail (if accessible)
POST   /api/v1/{object_type}/           Create (if rules allow)
PATCH  /api/v1/{object_type}/{id}/      Update (if rules allow)
DELETE /api/v1/{object_type}/{id}/      Tombstone (if rules allow)
```

### Schema from Registry

- Object types registered as `RegistryKind.INSTANCE` with field metadata
  → request/response schemas
- Rules bound to object types → authorization requirements in docs
- Scope projection model → visibility semantics in docs
- Audit ActionTypes → documented side effects

### OpenAPI Spec

The `/api/v1/openapi.json` endpoint generates the spec dynamically:

- Paths from registered object types
- Schemas from construct metadata (field names, types, descriptions)
- Security schemes from the identity layer (principal kinds, auth methods)
- Response codes from the rules engine (403 when denied, 404 when not projected)

---

## Stage 5: Metrics & Billing

The system surfaces aggregate metrics for operational visibility and
contractual billing. This is not an in-app payment system — it provides the
data needed to invoice via external billing (Stripe or equivalent).

### What Gets Measured

| Metric | Source | Purpose |
|--------|--------|---------|
| **Active principals** (heads) | `principals` table, `lifecycle = ACTIVE` | Per-seat billing |
| **Principal kinds breakdown** | `principals` table, grouped by `kind` | Tier pricing (users vs bots vs services) |
| **Object count by type** | `scoped_objects` table, grouped by `object_type` | Storage/usage billing |
| **Object versions** | `object_versions` table | Version history depth |
| **Active scopes** | `scopes` table, `lifecycle = ACTIVE` | Workspace billing |
| **Scope membership** | `scope_members` table | Collaboration metric |
| **Audit trail volume** | `audit_trail` table, count + time range | Compliance/retention billing |
| **Rule evaluations** | Audit entries with `action = access_check` | Policy engine usage |
| **API request volume** | Runtime request logs | Rate/usage billing |
| **Storage size** | DB file size / blob storage | Infrastructure cost |

### Aggregation

The metrics engine provides:

- **Point-in-time snapshots**: "Right now there are 47 active users"
- **Time-range aggregations**: "Last 30 days: 12,400 API requests"
- **Growth trends**: "Principals increased 15% month-over-month"
- **Per-scope breakdowns**: "Scope 'Team Alpha' has 8 members, 34 objects"

### Billing Surface

The metrics feed into a billing summary that maps to contractual line items:

```
Monthly Usage Summary — March 2026
───────────────────────────────────
Active Users (seats):        47  @ $X/seat
Active Service Accounts:      3  @ $Y/account
Active Scopes (workspaces):  12  @ $Z/workspace
Objects Stored:           1,204
Audit Entries:           18,340
API Requests:            52,100
───────────────────────────────────
```

This data is queryable via API (`/api/v1/metrics/`) for integration with
Stripe metered billing or manual invoice generation.

### Implementation Approach

The metrics layer is read-only aggregation over existing tables — it does not
require new storage. Every metric derives from data already captured by the
core layers:

- Identity (Layer 2) → principal counts
- Objects (Layer 3) → object/version counts
- Tenancy (Layer 4) → scope/membership counts
- Audit (Layer 6) → trail volume, action frequency
- Runtime request logs → API usage

A periodic snapshot job (via Layer 16: Scheduling) can materialize these
aggregates into a `metrics_snapshots` table for historical trending without
re-scanning the full tables on every query.

---

## Architecture Diagram

```
                    +------------------+
                    |   System Owner   |
                    +--------+---------+
                             |
                    +--------v---------+
                    |   System View    |
                    |   (/system/)     |
                    +--------+---------+
                             |
                    Registry + Manifest
                             |
              +--------------+--------------+
              |                             |
    +---------v----------+       +----------v---------+
    |   Runtime Engine   |       |  Metrics Dashboard  |
    |                    |       |                     |
    | - Auto-gen UI      |       | - Usage aggregation |
    | - Self-doc API     |       | - Billing summary   |
    | - Rule enforcement |       | - Growth trends     |
    | - Audit recording  |       | - Per-scope detail  |
    | - Scope filtering  |       |                     |
    +---------+----------+       +---------------------+
              |
    +---------v----------+
    |   Client Users     |
    | (scoped by rules)  |
    +--------------------+
```

---

## What Constitutes an Application

An application built on the Scoped platform is the sum of these components,
each configured by the operator on the System view and served by the Runtime
Engine:

### Core Components (required for any application)

- **Authentication Layer** — how principals prove their identity
  - Password-based login (hash stored via L11 Secrets)
  - API key authentication (for service/bot principals)
  - Magic link / invite code flow (operator generates, user resets)
  - SSO / OIDC / SAML integration (external identity provider)
  - Session management (token issuance, expiry, refresh)
- **Data Models** — object types with field schemas (L1 Models + A2 Contracts)
  - Auto-generated CRUD forms from schemas
  - Validation rules from contract field constraints
- **Identity Graph** — who exists and how they relate (L2 Identity)
  - Principal kinds (user, bot, team, org, service, api_key)
  - Relationships (member_of, owns, administers)
  - Metadata (email, department, external_id)
- **Access Control** — who can do what (L5 Rules)
  - Action-based policies (read, write, execute, share, deploy)
  - Scope-bound rules (rules apply within sharing boundaries)
  - Deny-overrides semantics
- **Tenancy** — sharing boundaries (L4 Scopes)
  - Scope membership (who belongs)
  - Object projection (what's visible)
  - Workspace isolation

### Operational Components (application-specific)

- **Workflows** — pipelines, stages, approval flows (L9 Flow)
- **Environments** — ephemeral workspaces, staging, promotion (L8)
- **Deployments** — graduation to external targets with gates (L10)
- **Automation** — event subscriptions, notifications, schedules (L14-16)
- **Integrations** — plugins, hooks, external systems (L12)
- **Federation** — cross-org connectors, marketplace (L13)

### Platform Components (always present)

- **Audit Trail** — immutable, hash-chained action log (L6)
- **Version History** — point-in-time reconstruction, rollback (L7)
- **Secrets Vault** — encrypted credential storage (L11)
- **Self-Documenting API** — OpenAPI spec generated from registry
- **Metrics** — usage aggregation, billing summary

### Runtime Engine Responsibilities

The Runtime Engine (`/`) is responsible for:

1. Reading the registry to discover what the operator has configured
2. Rendering appropriate UI surfaces for the authenticated principal
3. Enforcing rules on every action (the principal never sees what rules deny)
4. Recording audit entries for every mutation
5. Filtering all data through scope projections
6. Serving the auto-generated API with OpenAPI documentation
7. Managing sessions and mapping authenticated users to Scoped principals

The System view (`/system/`) is where the operator configures all of the above.
It is protected by Django auth and is not accessible to application end-users.

---

## Open Questions

1. **UI framework**: Server-rendered (Django templates, htmx) vs client-side
   (React) vs hybrid? The orchestrator uses Django+htmx for dashboard and
   React for canvas — the runtime UI could follow either pattern.

2. **Multi-tenancy at the platform level**: One orchestrator instance per
   customer, or one instance serving multiple isolated deployments? The
   Scoped architecture supports both — scopes can isolate at any level.

3. **Schema evolution**: When the operator changes an object type's fields on
   the canvas, how does the runtime UI handle existing data that doesn't
   match the new schema? Layer 7 (Temporal) and extension A1 (Migrations)
   are designed for this.

4. **Offline/edge**: Should the runtime engine support disconnected operation
   with sync-on-reconnect? The hash-chained audit trail makes conflict
   detection natural.
