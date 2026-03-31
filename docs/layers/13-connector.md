# Layer 13: Platform Connector & Marketplace

## Purpose

The connector is the crown jewel. It answers the question: **how do two separate organizations collaborate without surrendering their isolation?**

Every layer below this operates within a single Scoped instance — one organization's watershed. The connector bridges two watersheds. It's a governed canal between two rivers, built by mutual agreement, controlled by both sides, revocable at any moment.

The marketplace is the map that shows where canals can be built — a public discovery layer for connector templates, plugins, and integrations.

## Core Concepts

### Connector

A governed bridge between two Scoped instances.

| Field | Purpose |
|-------|---------|
| `name` | Connector label |
| `local_org_id` | Our organization (principal) |
| `remote_org_id` | Their organization (external identifier) |
| `remote_endpoint` | How to reach the other side |
| `state` | Lifecycle: proposed → pending_approval → active → suspended → revoked |
| `direction` | inbound, outbound, or bidirectional |
| `local_scope_id` | The connector scope on our side |

### Connector Lifecycle

```
proposed ──→ pending_approval ──→ active ──→ suspended ──→ active
    │                                │            │
    └──→ rejected                    └──→ revoked └──→ revoked
```

Both sides must approve. Either side can revoke — instantly, non-negotiable. Revocation is traced on both sides.

- **proposed**: One side proposes a connection. The other side hasn't seen it yet.
- **pending_approval**: The other side has received the proposal and is reviewing.
- **active**: Both sides approved. Data can flow.
- **suspended**: Temporarily paused by either side. No data flows, but the connection exists.
- **revoked**: Permanently terminated by either side. Immediate. All traffic stops.

### Mutual Agreement

Creating a connector is a two-phase commit:

1. **Org A proposes**: defines what they want to expose and what they want to receive
2. **Org B reviews**: sees the proposal, reviews the policy, decides whether to accept
3. **Org B approves**: defines their side (what they expose, what they accept)
4. **Both sides activate**: the connector scope is created on both sides, policies are applied

Neither side can force the other. The connector exists only by mutual consent.

### ConnectorPolicy

Rules governing what flows through the bridge.

| Field | Purpose |
|-------|---------|
| `connector_id` | Which connector |
| `policy_type` | allow_types, deny_types, rate_limit, classification |
| `config_json` | Policy configuration |

Policy types:

**allow_types** — only these object types can flow through.
**deny_types** — these object types are blocked.
**rate_limit** — maximum number of syncs per time period.
**classification** — objects with certain classifications (e.g., "confidential") are blocked.

Both sides define their own policies independently. Traffic must satisfy policies on BOTH sides.

**Hard rule: secrets NEVER flow through connectors.** This is enforced by the framework, not by policy. Even if both sides configure their policies to allow it, the framework blocks it.

### ConnectorTraffic

Every object that moves through a connector is logged.

| Field | Purpose |
|-------|---------|
| `connector_id` | Which connector |
| `direction` | inbound or outbound |
| `object_type` | What kind of object |
| `action` | sync, read, event |
| `status` | success, blocked, failed |
| `size_bytes` | Data volume |

This is separate from the audit trail for performance — connector traffic can be high-volume. But every traffic record is also traced in the main audit trail.

### Federation Protocol

How two Scoped instances communicate.

**Authentication**: Mutual TLS or pre-shared key exchange. Both sides authenticate.
**Schema negotiation**: On first connection, both sides declare their object types, versions, and capabilities. Incompatibilities are surfaced before any data flows.
**Message format**: Signed, timestamped, sequenced messages. Each side can verify the other's messages haven't been tampered with.
**Conflict resolution**: When both sides modify a shared object, the connector uses last-writer-wins with full audit trail on both sides. Applications can override with custom merge strategies.

### Marketplace

A public/global scope for discovering connector templates, plugins, and integrations.

#### MarketplaceListing

| Field | Purpose |
|-------|---------|
| `name` | Listing title |
| `publisher_id` | Who published it |
| `listing_type` | connector_template, plugin, integration |
| `version` | Semantic version |
| `config_template` | Template for creating instances |
| `visibility` | public, unlisted, private |
| `download_count` | Usage metric |

The marketplace is the **one exception** to user-first isolation. Listings in the marketplace are public by design — that's the point. But:
- Publishing is a traced action
- Listings are versioned
- Listings can be deprecated or removed
- The publisher controls the listing (they can update, deprecate, or pull it)

#### Installing from Marketplace

Installing a marketplace listing creates a **private instance**:
- A connector template becomes a private connector (both sides still need to approve)
- A plugin listing becomes a privately installed plugin (with its own scope)
- An integration listing becomes a private integration

The listing is a blueprint, not a live connection. Your instance is yours.

#### MarketplaceReview

| Field | Purpose |
|-------|---------|
| `listing_id` | Which listing |
| `reviewer_id` | Who reviewed |
| `rating` | 1-5 |
| `review_text` | Written feedback |

One review per principal per listing. Reviews are public within the marketplace scope.

## How It Connects

### To Layer 1 (Registry)
Connectors and marketplace listings are registered constructs.

### To Layer 4 (Tenancy)
Each connector creates a "connector scope" on each side. Objects projected into this scope are visible across the bridge. The connector scope follows all tenancy rules.

### To Layer 5 (Rules)
Connector policies are implemented as rules. Both sides' rules must be satisfied. Sharing rules can prohibit projection into connector scopes.

### To Layer 6 (Audit)
Every connector action is traced on both sides. Connector traffic is logged.

### To Layer 7 (Temporal)
Connector state changes are rollbackable (except revocation — that's permanent by design, though a new connector can be proposed).

### To Layer 8 (Environments)
Shared environments across connectors are possible — both sides project objects into a shared environment via the connector scope.

### To Layer 9 (Flow)
Connector traffic is a type of flow — objects moving between organizations through governed channels.

### To Layer 11 (Secrets)
Secrets NEVER flow through connectors. Connector authentication credentials are managed as secrets.

### To Layer 12 (Integrations)
Marketplace listings can be plugins or integrations. Installing from the marketplace goes through the integration/plugin lifecycle.

## Files

```
scoped/connector/
    __init__.py
    models.py          # Connector, ConnectorPolicy, ConnectorScope
    protocol.py        # Federation protocol, signed messages, schema negotiation
    bridge.py          # Data bridge — route objects through connectors
    marketplace/
        __init__.py
        models.py      # MarketplaceListing, MarketplaceScope, MarketplaceReview
        discovery.py   # Search, filter, browse marketplace
        publishing.py  # Publish, version, deprecate listings
```

## Schema

```sql
CREATE TABLE connectors (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    local_org_id    TEXT NOT NULL REFERENCES principals(id),
    remote_org_id   TEXT NOT NULL,
    remote_endpoint TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'proposed',
    direction       TEXT NOT NULL DEFAULT 'bidirectional',
    local_scope_id  TEXT REFERENCES scopes(id),
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    approved_at     TEXT,
    approved_by     TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE connector_policies (
    id              TEXT PRIMARY KEY,
    connector_id    TEXT NOT NULL REFERENCES connectors(id),
    policy_type     TEXT NOT NULL,
    config_json     TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL
);

CREATE TABLE connector_traffic (
    id              TEXT PRIMARY KEY,
    connector_id    TEXT NOT NULL REFERENCES connectors(id),
    direction       TEXT NOT NULL,
    object_type     TEXT NOT NULL,
    object_id       TEXT,
    action          TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'success',
    size_bytes      INTEGER,
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE marketplace_listings (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    publisher_id    TEXT NOT NULL REFERENCES principals(id),
    listing_type    TEXT NOT NULL,
    version         TEXT NOT NULL DEFAULT '1.0.0',
    config_template TEXT NOT NULL DEFAULT '{}',
    visibility      TEXT NOT NULL DEFAULT 'public',
    published_at    TEXT NOT NULL,
    updated_at      TEXT,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE',
    download_count  INTEGER NOT NULL DEFAULT 0,
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE marketplace_reviews (
    id              TEXT PRIMARY KEY,
    listing_id      TEXT NOT NULL REFERENCES marketplace_listings(id),
    reviewer_id     TEXT NOT NULL REFERENCES principals(id),
    rating          INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
    review_text     TEXT NOT NULL DEFAULT '',
    reviewed_at     TEXT NOT NULL,
    UNIQUE(listing_id, reviewer_id)
);

CREATE TABLE marketplace_installs (
    id              TEXT PRIMARY KEY,
    listing_id      TEXT NOT NULL REFERENCES marketplace_listings(id),
    installer_id    TEXT NOT NULL REFERENCES principals(id),
    installed_at    TEXT NOT NULL,
    version         TEXT NOT NULL,
    config_json     TEXT NOT NULL DEFAULT '{}',
    result_ref      TEXT,
    result_type     TEXT
);
```

## Invariants

1. Connectors require mutual approval — neither side can force the other.
2. Either side can revoke instantly — non-negotiable, immediate.
3. Secrets NEVER flow through connectors (framework-enforced, not policy).
4. Connector traffic must satisfy policies on BOTH sides.
5. All connector actions are traced on both sides independently.
6. Marketplace listings are the one intentional exception to user-first isolation — they're public by design.
7. Installing from marketplace creates a private instance, not a shared one.
