---
title: "Exceptions Reference"
description: "Complete reference of every exception class in pyscoped, organised by subsystem, with inheritance, trigger conditions, and handling guidance."
category: "API Reference"
---

# Exceptions Reference

Every exception in pyscoped inherits from `ScopedError` (with one noted
exception). This page documents every class, its parent, when it is raised,
and how to handle it.

## Base

### ScopedError

- **Parent:** `Exception`
- **Import:** `from scoped.exceptions import ScopedError`
- **Raised:** Never raised directly; serves as the base for all pyscoped exceptions.
- **Context dict:** Every `ScopedError` instance carries a `context` dict with structured metadata about the error (e.g., scope ID, principal ID, object type). Access it via `err.context`.

```python
from scoped.exceptions import ScopedError

try:
    client.create_scope(name="x", owner_id="u")
except ScopedError as e:
    print(e.context)  # {"scope_name": "x", "owner_id": "u", ...}
```

## Registry

### RegistryError

- **Parent:** `ScopedError`
- **Raised:** Base for all registry-related errors.

### NotRegisteredError

- **Parent:** `RegistryError`
- **Raised:** When accessing a type or plugin that has not been registered.
- **Handle:** Register the type before use, or guard with `registry.is_registered()`.

### AlreadyRegisteredError

- **Parent:** `RegistryError`
- **Raised:** When registering a type or plugin name that is already registered.
- **Handle:** Check `registry.is_registered()` before registering, or use `registry.register(name, ..., replace=True)` if re-registration is intentional.

### RegistryFrozenError

- **Parent:** `RegistryError`
- **Raised:** When attempting to modify a registry that has been frozen with `registry.freeze()`.
- **Handle:** Perform all registrations before freezing. Freezing is a one-way operation in production; in tests, use `registry.reset()`.

## Identity

### IdentityError

- **Parent:** `ScopedError`
- **Raised:** Base for identity-related errors.

### NoContextError

- **Parent:** `IdentityError`
- **Raised:** When an operation requires a principal context but none is active (e.g., no `principal_id` parameter and no ambient context).
- **Handle:** Pass `principal_id` explicitly or ensure the middleware has set the context.

### PrincipalNotFoundError

- **Parent:** `IdentityError`
- **Raised:** When the specified principal ID does not exist in the backend.
- **Handle:** Verify the principal was created before referencing it.

## Access

### AccessError

- **Parent:** `ScopedError`
- **Raised:** Base for access-control errors.

### AccessDeniedError

- **Parent:** `AccessError`
- **Raised:** When a principal attempts an action that is explicitly denied by a rule or implicitly disallowed by role.
- **Handle:** Check the principal's role and the applicable rules. The `context` dict contains `principal_id`, `action`, `scope_id`, and the denying `rule_id`.

```python
from scoped.exceptions import AccessDeniedError

try:
    client.delete_object(scope_id=sid, object_id=oid, principal_id=pid)
except AccessDeniedError as e:
    print(f"Denied by rule {e.context['rule_id']}")
```

### IsolationViolationError

- **Parent:** `AccessError`
- **Raised:** When an operation attempts to access an object outside its scope boundary. This is a hard isolation failure, not a permission issue.
- **Handle:** This indicates a bug in your application logic. Objects can only be accessed within their owning scope.

## Scope

### ScopeError

- **Parent:** `ScopedError`
- **Raised:** Base for scope-related errors.

### ScopeNotFoundError

- **Parent:** `ScopeError`
- **Raised:** When referencing a scope ID that does not exist.
- **Handle:** Verify the scope exists before operating on it.

### ScopeFrozenError

- **Parent:** `ScopeError`
- **Raised:** When attempting to modify a scope that has been frozen (made read-only).
- **Handle:** Unfreeze the scope first if the operation is intentional, or create a new scope.

## Rules

### RuleError

- **Parent:** `ScopedError`
- **Raised:** Base for rule-related errors.

### RuleConflictError

- **Parent:** `RuleError`
- **Raised:** When creating a rule that directly conflicts with an existing rule (same scope, role, action, but opposite effect).
- **Handle:** Remove or update the conflicting rule before creating the new one. The `context` dict contains the conflicting `rule_id`.

### RuleEvaluationError

- **Parent:** `RuleError`
- **Raised:** When the rule engine encounters an internal error during evaluation (e.g., malformed rule config).
- **Handle:** Inspect the rule configuration for syntax errors. This is typically a data issue, not a code issue.

### RateLimitExceededError

- **Parent:** `RuleError`
- **Raised:** When a rate-limit rule blocks an operation because the principal has exceeded the allowed request count within the time window.
- **Handle:** Back off and retry, or request a higher rate limit.

### QuotaExceededError

- **Parent:** `RuleError`
- **Raised:** When a quota rule blocks an operation because the scope or principal has reached a resource limit (e.g., max objects, max storage).
- **Handle:** Delete unused resources or request a quota increase.

## Audit

### AuditError

- **Parent:** `ScopedError`
- **Raised:** Base for audit-related errors.

### TraceIntegrityError

- **Parent:** `AuditError`
- **Raised:** When the audit chain's hash linkage is broken, indicating tampering or corruption.
- **Handle:** This is a critical integrity alert. Investigate the storage backend for corruption. Do not silently ignore this error.

## Temporal

### TemporalError

- **Parent:** `ScopedError`
- **Raised:** Base for temporal/versioning errors.

### RollbackDeniedError

- **Parent:** `TemporalError`
- **Raised:** When a rollback is denied by policy (e.g., the scope does not allow rollbacks, or the target version is too old).
- **Handle:** Check the scope's rollback policy and the target version.

### RollbackFailedError

- **Parent:** `TemporalError`
- **Raised:** When a rollback operation fails during execution (e.g., the target version's data is incompatible with the current schema).
- **Handle:** Inspect the version data and current schema. Manual reconciliation may be needed.

## Environment

### EnvironmentError

- **Parent:** `ScopedError`
- **Raised:** Base for environment-related errors.

### EnvironmentStateError

- **Parent:** `EnvironmentError`
- **Raised:** When an invalid environment state transition is attempted (e.g., promoting from a non-promotable stage).
- **Handle:** Check the environment's current state and the allowed transitions.

### EnvironmentNotFoundError

- **Parent:** `EnvironmentError`
- **Raised:** When referencing an environment ID that does not exist.
- **Handle:** Verify the environment exists.

## Flow

### FlowError

- **Parent:** `ScopedError`
- **Raised:** Base for deployment-flow errors.

### StageTransitionDeniedError

- **Parent:** `FlowError`
- **Raised:** When a stage transition is denied by a gate check or policy.
- **Handle:** Inspect which gate failed via `e.context["gate"]`.

### FlowBlockedError

- **Parent:** `FlowError`
- **Raised:** When the flow is blocked by an upstream dependency or manual hold.
- **Handle:** Resolve the blocking condition before retrying.

### PromotionDeniedError

- **Parent:** `FlowError`
- **Raised:** When promoting an environment is denied (e.g., required approvals are missing).
- **Handle:** Ensure all required approvals and gate checks pass before promoting.

## Deployment

### DeploymentError

- **Parent:** `ScopedError`
- **Raised:** Base for deployment errors.

### DeploymentGateFailedError

- **Parent:** `DeploymentError`
- **Raised:** When a deployment gate check fails (e.g., test suite failure, security scan finding).
- **Handle:** Fix the gate failure and re-trigger the deployment.

### DeploymentRollbackError

- **Parent:** `DeploymentError`
- **Raised:** When a deployment rollback fails.
- **Handle:** Manual intervention is typically required. Check the deployment log for details.

## Secret

### SecretError

- **Parent:** `ScopedError`
- **Raised:** Base for secret-management errors.

### SecretNotFoundError

- **Parent:** `SecretError`
- **Raised:** When referencing a secret ID that does not exist.
- **Handle:** Verify the secret was created and has not been deleted.

### SecretAccessDeniedError

- **Parent:** `SecretError`
- **Raised:** When a principal lacks permission to read, update, or rotate a secret, or when an attempt is made to pass a secret through a connector.
- **Handle:** Check the principal's secret-access grants.

### SecretRefExpiredError

- **Parent:** `SecretError`
- **Raised:** When a time-limited secret reference has expired and the secret value can no longer be retrieved through that reference.
- **Handle:** Request a new secret reference.

### SecretRotationError

- **Parent:** `SecretError`
- **Raised:** When automatic or manual secret rotation fails (e.g., the rotation callback raises an exception).
- **Handle:** Inspect the rotation callback and retry. The old secret value remains active until rotation succeeds.

### SecretLeakDetectedError

- **Parent:** `SecretError`
- **Raised:** When the leak-detection subsystem identifies a secret value in an unauthorized location (logs, object data, connector payload).
- **Handle:** Rotate the secret immediately and investigate the leak vector.

## Integration

### IntegrationError

- **Parent:** `ScopedError`
- **Raised:** Base for plugin and hook errors.

### PluginError

- **Parent:** `IntegrationError`
- **Raised:** When a plugin fails to load, initialise, or execute.
- **Handle:** Check the plugin's logs and configuration.

### PluginPermissionError

- **Parent:** `IntegrationError`
- **Raised:** When a plugin attempts an operation outside its declared permission set.
- **Handle:** Update the plugin's permission manifest or remove the offending call.

### PluginSandboxError

- **Parent:** `IntegrationError`
- **Raised:** When a plugin violates sandbox constraints (e.g., file-system access, network access outside allowed hosts).
- **Handle:** Restrict the plugin's behaviour or adjust sandbox policy.

### HookExecutionError

- **Parent:** `IntegrationError`
- **Raised:** When a lifecycle hook raises an unhandled exception.
- **Handle:** Fix the hook implementation. The `context` dict contains the hook name, event, and the original exception.

## Connector

### ConnectorError

- **Parent:** `ScopedError`
- **Raised:** Base for connector errors, also raised on invalid state transitions.

### ConnectorNotApprovedError

- **Parent:** `ConnectorError`
- **Raised:** When attempting to sync through a connector that is not in `ACTIVE` state.
- **Handle:** Ensure the connector has been approved and is not suspended or revoked.

### ConnectorRevokedError

- **Parent:** `ConnectorError`
- **Raised:** When attempting any operation on a permanently revoked connector.
- **Handle:** Revocation is terminal. Create a new connector if the integration is still needed.

### ConnectorPolicyViolation

- **Parent:** `ConnectorError`
- **Raised:** When a sync operation violates an attached policy (disallowed type, rate limit exceeded, classification too high).
- **Handle:** Check which policy was violated via `e.context["policy_type"]` and adjust the payload or policy.

### FederationError

- **Parent:** `ConnectorError`
- **Raised:** When federation message signing, verification, or schema negotiation fails.
- **Handle:** Verify keys are correct and schemas are compatible.

## Marketplace

### MarketplaceError

- **Parent:** `ScopedError`
- **Raised:** Base for marketplace errors.

### ListingNotFoundError

- **Parent:** `MarketplaceError`
- **Raised:** When referencing a marketplace listing ID that does not exist.
- **Handle:** Verify the listing ID.

## Contract

### ContractError

- **Parent:** `ScopedError`
- **Raised:** Base for contract errors.

### ContractNotFoundError

- **Parent:** `ContractError`
- **Raised:** When referencing a contract ID that does not exist.

### ContractValidationError

- **Parent:** `ContractError`
- **Raised:** When contract terms fail validation (e.g., missing required fields, invalid date ranges).
- **Handle:** Fix the contract data and retry.

## Template

### TemplateError

- **Parent:** `ScopedError`
- **Raised:** Base for template errors.

### TemplateNotFoundError

- **Parent:** `TemplateError`
- **Raised:** When referencing a template ID that does not exist.

### TemplateVersionNotFoundError

- **Parent:** `TemplateError`
- **Raised:** When referencing a specific version of a template that does not exist.

### TemplateInstantiationError

- **Parent:** `TemplateError`
- **Raised:** When instantiating a template fails (e.g., required variables are missing, validation of generated output fails).
- **Handle:** Check the template's required variables and provide valid values.

## Event

### EventError

- **Parent:** `ScopedError`
- **Raised:** Base for event-system errors.

### EventNotFoundError

- **Parent:** `EventError`
- **Raised:** When referencing an event ID that does not exist.

### SubscriptionNotFoundError

- **Parent:** `EventError`
- **Raised:** When referencing a subscription ID that does not exist.

### WebhookDeliveryError

- **Parent:** `EventError`
- **Raised:** When webhook delivery fails permanently (after exhausting all retries).
- **Handle:** Check the endpoint URL, verify it is reachable, and inspect `e.context["last_error"]` for details.

## Notification

### NotificationError

- **Parent:** `ScopedError`
- **Raised:** Base for notification errors.

### NotificationNotFoundError

- **Parent:** `NotificationError`
- **Raised:** When referencing a notification ID that does not exist.

## Scheduling

### SchedulingError

- **Parent:** `ScopedError`
- **Raised:** Base for scheduling errors.

### JobNotFoundError

- **Parent:** `SchedulingError`
- **Raised:** When referencing a job ID that does not exist.

### JobStateError

- **Parent:** `SchedulingError`
- **Raised:** When attempting an invalid job state transition (e.g., cancelling a completed job, running a cancelled job).
- **Handle:** Check the job's current state before attempting the operation.

## Migration

### MigrationError

- **Parent:** `ScopedError`
- **Raised:** When a migration fails to apply or rollback, or when a checksum mismatch is detected.
- **Handle:** Inspect the migration file and database state. Do not re-apply migrations with checksum mismatches without understanding why the file changed.

## Compliance

### ComplianceError

- **Parent:** `ScopedError`
- **Raised:** Base for compliance errors.

### ComplianceViolation

- **Parent:** `ComplianceError`
- **Raised:** When an operation violates a compliance rule (e.g., data residency, retention policy, classification constraint).
- **Handle:** Review the compliance rules attached to the scope and adjust the operation accordingly.

## Sync

### SyncError

- **Parent:** `ScopedError`
- **Raised:** Base for sync-agent errors.

### SyncNotConfiguredError

- **Parent:** `SyncError`
- **Raised:** When starting the sync agent without providing a `SyncConfig`.
- **Handle:** Pass a valid `SyncConfig` when constructing the `SyncAgent`.

### SyncTransportError

- **Parent:** `SyncError`
- **Raised:** When the HTTP transport layer fails (connection refused, timeout, non-2xx response).
- **Handle:** Check network connectivity and the remote server's health.

### SyncAuthenticationError

- **Parent:** `SyncError`
- **Raised:** When the remote server rejects the sync agent's credentials (HTTP 401/403).
- **Handle:** Verify the API key or token used by the sync agent.

### SyncBatchRejectedError

- **Parent:** `SyncError`
- **Raised:** When the remote server rejects an entire batch (e.g., schema mismatch, version conflict).
- **Handle:** Inspect `e.context["rejected_ids"]` and reconcile the data.

### SyncVerificationError

- **Parent:** `SyncError`
- **Raised:** When `verify_sync()` detects mismatches between local and remote data.
- **Handle:** Review the verification report and reconcile manually.

## TenantRouter

### TenantResolutionError

- **Parent:** `RuntimeError` (not `ScopedError`)
- **Raised:** When the tenant router middleware cannot determine the tenant from the incoming request (e.g., missing header, unknown subdomain, invalid JWT claim).
- **Handle:** Ensure the request includes the expected tenant identifier. This inherits from `RuntimeError` rather than `ScopedError` because it occurs before the pyscoped context is established.

```python
from scoped.exceptions import TenantResolutionError

try:
    response = test_client.get("/api/data")
except TenantResolutionError:
    # Tenant header was missing or unrecognised
    pass
```

## Catching exceptions by category

Because the hierarchy is well-structured, you can catch broad categories:

```python
from scoped.exceptions import (
    AccessError,
    ScopedError,
    SecretError,
    SyncError,
)

try:
    perform_complex_operation()
except AccessError:
    # Any access-related denial
    return 403
except SecretError:
    # Any secret-management failure
    return 500
except SyncError:
    # Any sync failure -- log and retry later
    schedule_retry()
except ScopedError as e:
    # Catch-all for everything else in pyscoped
    log.error("Unexpected scoped error", context=e.context)
    return 500
```
