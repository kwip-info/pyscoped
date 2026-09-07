# Layer 11: Secrets

## Purpose

Secrets are the tightest isolation boundary in the system. API keys, tokens, credentials, certificates, connection strings — anything that would be catastrophic if leaked.

Secrets follow every rule that every other object follows (registry, scoping, audit, versioning) plus additional constraints that make them the most restricted construct in the framework.

## Core Concepts

### Secret

An encrypted-at-rest value.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `name` | Human-readable label ("stripe-api-key", "db-prod-password") |
| `owner_id` | Principal who created it |
| `object_id` | Link to scoped_objects — secrets ARE scoped objects |
| `classification` | standard, sensitive, critical |
| `expires_at` | Optional expiry |
| `last_rotated_at` | When the value was last changed |

Secrets are scoped objects. This means they get versioning, isolation, and lifecycle management for free from Layer 3. The secrets layer adds encryption, refs, and policies on top.

### SecretVersion

Each rotation creates a new version. The value is encrypted before storage.

| Field | Purpose |
|-------|---------|
| `secret_id` | Which secret |
| `version` | Version number |
| `encrypted_value` | Ciphertext — NEVER plaintext |
| `encryption_algo` | What algorithm (default: Fernet/AES-256) |
| `key_id` | Which encryption key was used |

Old versions are retained (encrypted) for rollback. If a rotation breaks something, you can roll back to the previous version.

### SecretRef

The critical abstraction. Components never receive secret values directly — they receive **refs**.

| Field | Purpose |
|-------|---------|
| `secret_id` | Which secret this refs |
| `ref_token` | Opaque token — this is what gets passed around |
| `granted_to` | Which principal can resolve this ref |
| `scope_id` | Scope this ref is valid within (nullable = any scope the principal has access to) |
| `environment_id` | Environment this ref is valid within (nullable = any environment) |
| `expires_at` | When this ref stops working |
| `lifecycle` | ACTIVE or REVOKED |

The resolution flow:
1. Component receives a ref token
2. Component calls the vault to resolve it
3. Vault checks: is the acting principal the `granted_to` principal?
4. Vault checks: is the current scope/environment valid for this ref?
5. Vault checks: is the ref expired or revoked?
6. If all checks pass: decrypt and return the value
7. If any check fails: raise `SecretAccessDeniedError` or `SecretRefExpiredError`

**Every resolution is traced** in the `secret_access_log`. But the trace records that access happened — it does NOT record the value.

### SecretPolicy

Rules specific to secret lifecycle.

| Field | Purpose |
|-------|---------|
| `secret_id` | Specific secret (nullable = applies to classification) |
| `classification` | Applies to all secrets of this classification |
| `max_age_seconds` | Maximum time before rotation required |
| `auto_rotate` | Whether to auto-rotate on expiry |
| `allowed_scopes` | JSON array of scope IDs where this secret can be used |
| `allowed_envs` | JSON array of environment IDs where this secret can be used |

### SecretBackend

Pluggable encryption. The default uses Fernet (AES-256-CBC with HMAC). For production:
- HSM (Hardware Security Module) integration
- AWS KMS / GCP KMS / Azure Key Vault
- HashiCorp Vault

The secret backend is a registered construct — swapping it is a traced, audited action.

### Leak Detection

The framework monitors for plaintext secret values appearing where they shouldn't:
- In audit trail `before_state`/`after_state`
- In environment snapshots
- In connector traffic
- In object version `data_json` (if the value matches a known secret)

If detected: `SecretLeakDetectedError` is raised immediately and the action is blocked. The detection itself is traced as a critical audit event.

## How It Connects

### To Layer 3 (Objects)
Secrets ARE scoped objects. They have an `object_id` link. This gives them versioning, ownership, lifecycle, and isolation for free.

### To Layer 4 (Tenancy)
Secrets are shared via scope projections, just like any other object. Secret refs are additionally scoped — a ref can be restricted to a specific scope.

### To Layer 5 (Rules)
Secret policies are implemented as rules. Access restrictions, rotation requirements, and classification-based policies all go through the rule engine.

### To Layer 6 (Audit)
Secret access is traced in the `secret_access_log` (a dedicated table for high-frequency access events) AND in the main audit trail. Secret values are NEVER included in trace states.

### To Layer 7 (Temporal)
Secret rotations can be rolled back (restoring a previous encrypted version). Rollback of secrets may be restricted by policy — if a secret was rotated because the old value was compromised, rollback should be blocked.

### To Layer 8 (Environments)
Environments can hold secret refs for credentials needed during work. When an environment is discarded, its secret refs are revoked. Environment snapshots never include secret values.

### To Layer 12 (Integrations)
Integration credentials are stored as secrets with refs. Plugin secret access is governed by plugin permissions.

### To Layer 13 (Connector)
**Secrets NEVER flow through connectors.** This is a hard rule. Connector policies enforce it, and the compliance engine validates it.

## Files

```
scoped/secrets/
    __init__.py
    models.py          # Secret, SecretRef, SecretVersion
    vault.py           # Encrypt/decrypt, key management, ref resolution
    policy.py          # SecretPolicy, rotation, access restrictions
    backend.py         # SecretBackend interface + default Fernet implementation
    leak_detection.py  # Detect plaintext secret values in non-secret contexts
```

## Schema

```sql
CREATE TABLE secrets (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    owner_id        TEXT NOT NULL REFERENCES principals(id),
    object_id       TEXT NOT NULL REFERENCES scoped_objects(id),
    current_version INTEGER NOT NULL DEFAULT 1,
    classification  TEXT NOT NULL DEFAULT 'standard',
    created_at      TEXT NOT NULL,
    expires_at      TEXT,
    last_rotated_at TEXT,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE secret_versions (
    id              TEXT PRIMARY KEY,
    secret_id       TEXT NOT NULL REFERENCES secrets(id),
    version         INTEGER NOT NULL,
    encrypted_value TEXT NOT NULL,
    encryption_algo TEXT NOT NULL DEFAULT 'fernet',
    key_id          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    UNIQUE(secret_id, version)
);

CREATE TABLE secret_refs (
    id              TEXT PRIMARY KEY,
    secret_id       TEXT NOT NULL REFERENCES secrets(id),
    ref_token       TEXT NOT NULL UNIQUE,
    granted_to      TEXT NOT NULL REFERENCES principals(id),
    scope_id        TEXT,
    environment_id  TEXT,
    granted_at      TEXT NOT NULL,
    granted_by      TEXT NOT NULL,
    expires_at      TEXT,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE secret_access_log (
    id              TEXT PRIMARY KEY,
    secret_id       TEXT NOT NULL REFERENCES secrets(id),
    ref_id          TEXT,
    accessor_id     TEXT NOT NULL REFERENCES principals(id),
    access_type     TEXT NOT NULL,
    accessed_at     TEXT NOT NULL,
    environment_id  TEXT,
    scope_id        TEXT,
    result          TEXT NOT NULL DEFAULT 'success'
);

CREATE TABLE secret_policies (
    id              TEXT PRIMARY KEY,
    secret_id       TEXT REFERENCES secrets(id),
    classification  TEXT,
    max_age_seconds INTEGER,
    auto_rotate     INTEGER NOT NULL DEFAULT 0,
    allowed_scopes  TEXT NOT NULL DEFAULT '[]',
    allowed_envs    TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL
);
```

## Invariants

1. Secret values are NEVER stored in plaintext. Encrypted at rest, always.
2. Secret values NEVER appear in audit trail states.
3. Secret values NEVER appear in environment snapshots.
4. Secret values NEVER flow through connectors.
5. Secret refs are scope-checked on every dereference — revocation is immediate.
6. Every secret access (including denied attempts) is logged.
7. Leak detection blocks actions that would expose plaintext values.
