---
title: "Secrets API"
description: "API reference for SecretsNamespace -- encrypted secret storage, rotation, reference-based access, and security model."
category: "API Reference"
---

# Secrets API

The `SecretsNamespace` provides encrypted secret storage with rotation, reference-based
sharing, and audit-safe access. Secret values are encrypted at rest using Fernet
symmetric encryption and are **never** written to the audit trail. Access is mediated
through opaque reference tokens.

Access the namespace through the client:

```python
from scoped.client import ScopedClient

client = ScopedClient(database_url="sqlite:///app.db")
secrets = client.secrets
```

---

## Methods

### create

```python
secrets.create(
    name: str,
    value: str,
    owner_id: str | None = None,
    description: str | None = None,
    classification: str = "general",
) -> tuple[Secret, SecretVersion]
```

Creates a new encrypted secret and its initial version. The plaintext `value` is
encrypted before storage; only the ciphertext is persisted. The operation is recorded
in the audit trail, but the value itself is **never** included.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | *required* | Human-readable name for the secret. Must be unique within the owner's namespace. |
| `value` | `str` | *required* | The plaintext secret value to encrypt and store. |
| `owner_id` | `str \| None` | `None` | Principal ID of the secret owner. Falls back to the context principal or `SYSTEM`. |
| `description` | `str \| None` | `None` | Optional description of what this secret is used for. |
| `classification` | `str` | `"general"` | Security classification: `"general"`, `"sensitive"`, `"critical"`. Affects leak detection thresholds and rotation policy enforcement. |

#### Returns

A tuple of `(Secret, SecretVersion)`. The `Secret` model contains metadata (never
the plaintext). The `SecretVersion` contains version metadata and the encrypted
ciphertext.

#### Raises

| Exception | Condition |
|---|---|
| `DuplicateSecretError` | A secret with this name already exists for the owner. |
| `ValidationError` | `name` is empty, or `value` is empty. |

#### Example

```python
with client.as_principal(admin):
    secret, v1 = client.secrets.create(
        name="DATABASE_PASSWORD",
        value="supersecret123",
        description="Production database password",
        classification="critical",
    )
    print(secret.id)                # UUID
    print(secret.name)              # "DATABASE_PASSWORD"
    print(secret.classification)    # "critical"
    print(v1.version_number)        # 1
    # Note: v1 does NOT expose the plaintext value
```

---

### rotate

```python
secrets.rotate(
    secret_id: str,
    new_value: str,
    rotated_by: str | None = None,
    reason: str | None = None,
) -> SecretVersion
```

Creates a new version of an existing secret with an updated encrypted value. The
previous version is retained for audit purposes but is no longer resolvable through
existing references. All active `SecretRef` tokens continue to resolve to the
**latest** version.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `secret_id` | `str` | *required* | The secret to rotate. |
| `new_value` | `str` | *required* | The new plaintext value. Encrypted before storage. |
| `rotated_by` | `str \| None` | `None` | Principal performing the rotation. Falls back to the context principal. |
| `reason` | `str \| None` | `None` | Reason for rotation (e.g. `"scheduled rotation"`, `"credential leak"`). Recorded in the audit trail. |

#### Returns

A new `SecretVersion` instance representing the rotated value.

#### Raises

| Exception | Condition |
|---|---|
| `SecretNotFoundError` | The secret does not exist. |
| `PermissionDeniedError` | The principal is not the owner and has no admin grant. |

#### Example

```python
with client.as_principal(admin):
    v2 = client.secrets.rotate(
        secret.id,
        new_value="newsecret456",
        reason="Quarterly rotation",
    )
    print(v2.version_number)  # 2

    # Existing references now resolve to the new value
    plaintext = client.secrets.resolve(ref.ref_token, accessor_id=service.id)
    assert plaintext == "newsecret456"
```

---

### grant_ref

```python
secrets.grant_ref(
    secret_id: str,
    principal: str | Principal,
    granted_by: str | None = None,
    scope_id: str | None = None,
    environment_id: str | None = None,
    expires_at: datetime | None = None,
) -> SecretRef
```

Creates an opaque reference token that allows a specific principal to resolve (read)
the secret's current value. References can be scoped to a particular scope and/or
environment and can have an expiration time.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `secret_id` | `str` | *required* | The secret to grant access to. |
| `principal` | `str \| Principal` | *required* | The principal receiving access. |
| `granted_by` | `str \| None` | `None` | The principal granting access. Falls back to the context principal. Must be the secret owner or an admin. |
| `scope_id` | `str \| None` | `None` | Restrict the reference to this scope. The accessor must present this scope ID when resolving. |
| `environment_id` | `str \| None` | `None` | Restrict the reference to this environment (e.g. `"production"`, `"staging"`). |
| `expires_at` | `datetime \| None` | `None` | UTC expiration timestamp. After this time, `resolve` raises `SecretRefExpiredError`. `None` means the reference does not expire. |

#### Returns

A `SecretRef` model instance containing the opaque `ref_token`.

#### Raises

| Exception | Condition |
|---|---|
| `SecretNotFoundError` | The secret does not exist. |
| `PermissionDeniedError` | The granting principal is not the owner and has no admin access. |
| `PrincipalNotFoundError` | The target principal does not exist. |

#### Example

```python
from datetime import datetime, timedelta, timezone

with client.as_principal(admin):
    ref = client.secrets.grant_ref(
        secret.id,
        principal=deploy_service,
        scope_id=prod_scope.id,
        environment_id="production",
        expires_at=datetime.now(timezone.utc) + timedelta(days=90),
    )
    print(ref.ref_token)  # "sref_a1b2c3d4e5..."  (opaque)
```

---

### resolve

```python
secrets.resolve(
    ref_token: str,
    accessor_id: str | None = None,
    scope_id: str | None = None,
    environment_id: str | None = None,
) -> str
```

Resolves a reference token to the plaintext secret value. The accessor, scope, and
environment must match the constraints set when the reference was granted. Every
resolve call is recorded in the audit trail (without the plaintext value).

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `ref_token` | `str` | *required* | The opaque reference token obtained from `grant_ref`. |
| `accessor_id` | `str \| None` | `None` | The principal resolving the secret. Falls back to the context principal. Must match the principal the ref was granted to. |
| `scope_id` | `str \| None` | `None` | The scope context for resolution. Must match if the ref was scope-restricted. |
| `environment_id` | `str \| None` | `None` | The environment context. Must match if the ref was environment-restricted. |

#### Returns

The decrypted plaintext secret value as a string.

#### Raises

| Exception | Condition |
|---|---|
| `SecretRefNotFoundError` | The reference token is invalid or has been revoked. |
| `SecretRefExpiredError` | The reference has expired. |
| `PermissionDeniedError` | The accessor does not match the granted principal. |
| `ScopeMismatchError` | The provided `scope_id` does not match the ref's scope constraint. |
| `EnvironmentMismatchError` | The provided `environment_id` does not match the ref's environment constraint. |

#### Example

```python
with client.as_principal(deploy_service):
    password = client.secrets.resolve(
        ref.ref_token,
        scope_id=prod_scope.id,
        environment_id="production",
    )
    # password is the decrypted plaintext string
    connection = database.connect(password=password)
```

---

## Security Model

### Encryption at Rest

All secret values are encrypted using **Fernet symmetric encryption** (AES-128-CBC
with HMAC-SHA256 authentication). The encryption key is derived from the client's
API key or a dedicated key configured in the storage backend.

```
plaintext -> Fernet.encrypt() -> ciphertext (stored in DB)
ciphertext -> Fernet.decrypt() -> plaintext (returned by resolve)
```

### Opaque Reference Tokens

Reference tokens (`ref_token`) are cryptographically random, URL-safe strings
prefixed with `sref_`. They are not JWTs and contain no embedded information.
Resolution requires a database lookup, meaning tokens can be revoked server-side
at any time.

```
Format: sref_<44 URL-safe base64 characters>
Example: sref_dGhpcyBpcyBhIHRlc3QgdG9rZW4gZm9yIGRvY3M
```

### Values Never in Audit Trail

The audit trail records that a secret was created, rotated, granted, or resolved,
but the plaintext value is **never** stored in any audit entry. The `before_state`
and `after_state` fields for secret operations contain only metadata (name,
classification, version number).

### Leak Detection

When `classification` is set to `"sensitive"` or `"critical"`, the secrets engine
monitors resolve patterns for anomalies:

- Unusually high resolve frequency
- Resolves from unexpected principals (via granted refs)
- Resolves outside normal time windows

Detected anomalies are recorded as audit entries with action
`ActionType.SECRET_LEAK_DETECTED`.

### Access Control

By default, only the secret's owner can:
- Rotate the secret
- Grant reference tokens
- Resolve the secret directly (without a ref)

Other principals must receive a `SecretRef` via `grant_ref` to access the value.
The ref can be constrained by scope, environment, and expiration.

---

## Models

### Secret

Metadata-only model. Never contains the plaintext value.

```python
@dataclass(frozen=True)
class Secret:
    id: str
    name: str
    owner_id: str
    description: str | None
    classification: str
    current_version: int
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]
```

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique secret identifier (UUID v4). |
| `name` | `str` | Human-readable name. |
| `owner_id` | `str` | ID of the owning principal. |
| `description` | `str \| None` | Description of the secret's purpose. |
| `classification` | `str` | `"general"`, `"sensitive"`, or `"critical"`. |
| `current_version` | `int` | The latest version number. |
| `created_at` | `datetime` | UTC timestamp of creation. |
| `updated_at` | `datetime` | UTC timestamp of last rotation. |
| `metadata` | `dict[str, Any]` | Arbitrary metadata. |

### SecretVersion

Represents a single version of a secret. Contains the ciphertext but not the
plaintext.

```python
@dataclass(frozen=True)
class SecretVersion:
    id: str
    secret_id: str
    version_number: int
    created_at: datetime
    created_by: str
    reason: str | None
```

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique version identifier. |
| `secret_id` | `str` | Parent secret ID. |
| `version_number` | `int` | Sequential version number starting at 1. |
| `created_at` | `datetime` | UTC timestamp of version creation. |
| `created_by` | `str` | Principal ID that created or rotated this version. |
| `reason` | `str \| None` | Rotation reason, if provided. |

### SecretRef

An opaque reference granting a specific principal access to a secret.

```python
@dataclass(frozen=True)
class SecretRef:
    id: str
    secret_id: str
    ref_token: str
    principal_id: str
    granted_by: str
    scope_id: str | None
    environment_id: str | None
    expires_at: datetime | None
    created_at: datetime
    revoked: bool
```

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique ref identifier. |
| `secret_id` | `str` | The secret this ref points to. |
| `ref_token` | `str` | The opaque token used for `resolve()`. |
| `principal_id` | `str` | The principal authorized to resolve. |
| `granted_by` | `str` | The principal that created this ref. |
| `scope_id` | `str \| None` | Scope restriction, if any. |
| `environment_id` | `str \| None` | Environment restriction, if any. |
| `expires_at` | `datetime \| None` | Expiration timestamp, or `None` for no expiry. |
| `created_at` | `datetime` | UTC timestamp of grant creation. |
| `revoked` | `bool` | Whether the ref has been revoked. |

---

## Complete Example

```python
from datetime import datetime, timedelta, timezone
from scoped.client import ScopedClient

with ScopedClient(database_url="sqlite:///app.db") as client:
    # Create principals
    admin = client.principals.create(display_name="Admin", kind="user")
    deploy_svc = client.principals.create(display_name="Deploy", kind="service")
    monitoring = client.principals.create(display_name="Monitor", kind="service")

    with client.as_principal(admin):
        # Create a production scope
        prod = client.scopes.create(name="production", visibility="private")

        # Store a secret
        db_secret, v1 = client.secrets.create(
            name="POSTGRES_PASSWORD",
            value="initial_password_123",
            description="Production database password",
            classification="critical",
        )

        # Grant access to the deploy service (scoped + time-limited)
        deploy_ref = client.secrets.grant_ref(
            db_secret.id,
            principal=deploy_svc,
            scope_id=prod.id,
            environment_id="production",
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )

        # Grant access to monitoring (no scope restriction, no expiry)
        monitor_ref = client.secrets.grant_ref(
            db_secret.id,
            principal=monitoring,
        )

    # Deploy service resolves the secret
    with client.as_principal(deploy_svc):
        password = client.secrets.resolve(
            deploy_ref.ref_token,
            scope_id=prod.id,
            environment_id="production",
        )
        print(f"Resolved: {password}")  # "initial_password_123"

    # Admin rotates the secret
    with client.as_principal(admin):
        v2 = client.secrets.rotate(
            db_secret.id,
            new_value="rotated_password_456",
            reason="Quarterly rotation policy",
        )
        print(f"Rotated to version {v2.version_number}")  # 2

    # Deploy service gets the new value automatically
    with client.as_principal(deploy_svc):
        new_password = client.secrets.resolve(
            deploy_ref.ref_token,
            scope_id=prod.id,
            environment_id="production",
        )
        assert new_password == "rotated_password_456"

    # Audit trail records all operations (without plaintext)
    secret_trail = client.audit.for_object(db_secret.id)
    for entry in secret_trail:
        print(f"{entry.action}: {entry.metadata}")
        # secret.resolve: {"accessor_id": "...", "ref_id": "..."}
        # secret.rotate:  {"version": 2, "reason": "Quarterly rotation policy"}
        # secret.grant:   {"principal_id": "...", "scope_id": "..."}
        # secret.create:  {"name": "POSTGRES_PASSWORD", "classification": "critical"}
```
