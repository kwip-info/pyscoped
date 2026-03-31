"""All framework exceptions.

Every exception carries structured context so audit/trace can capture it.
"""

from __future__ import annotations

from typing import Any


class ScopedError(Exception):
    """Base exception for all Scoped framework errors."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None):
        super().__init__(message)
        self.context = context or {}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class RegistryError(ScopedError):
    """Base for registry-related errors."""


class NotRegisteredError(RegistryError):
    """Raised when accessing a construct that is not registered."""


class AlreadyRegisteredError(RegistryError):
    """Raised when attempting to register a construct that already exists."""


class RegistryFrozenError(RegistryError):
    """Raised when attempting to modify a frozen registry."""


# ---------------------------------------------------------------------------
# Identity / Context
# ---------------------------------------------------------------------------

class IdentityError(ScopedError):
    """Base for identity-related errors."""


class NoContextError(IdentityError):
    """Raised when an operation is attempted without a ScopedContext."""


class PrincipalNotFoundError(IdentityError):
    """Raised when a referenced principal does not exist."""


# ---------------------------------------------------------------------------
# Isolation / Access
# ---------------------------------------------------------------------------

class AccessError(ScopedError):
    """Base for access-related errors."""


class AccessDeniedError(AccessError):
    """Raised when a principal lacks permission for an action."""


class IsolationViolationError(AccessError):
    """Raised when an operation would breach isolation boundaries."""


# ---------------------------------------------------------------------------
# Scoping / Tenancy
# ---------------------------------------------------------------------------

class ScopeError(ScopedError):
    """Base for scope-related errors."""


class ScopeNotFoundError(ScopeError):
    """Raised when a referenced scope does not exist."""


class ScopeFrozenError(ScopeError):
    """Raised when attempting to modify a frozen scope."""


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

class RuleError(ScopedError):
    """Base for rule-related errors."""


class RuleConflictError(RuleError):
    """Raised when rules produce contradictory outcomes."""


class RuleEvaluationError(RuleError):
    """Raised when rule evaluation fails."""


class RateLimitExceededError(RuleError):
    """Raised when an action exceeds its rate limit."""


class QuotaExceededError(RuleError):
    """Raised when a resource creation would exceed its quota."""


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

class AuditError(ScopedError):
    """Base for audit-related errors."""


class TraceIntegrityError(AuditError):
    """Raised when audit trail integrity check fails (tamper detected)."""


# ---------------------------------------------------------------------------
# Temporal / Rollback
# ---------------------------------------------------------------------------

class TemporalError(ScopedError):
    """Base for temporal/rollback errors."""


class RollbackDeniedError(TemporalError):
    """Raised when a rollback is prohibited by rules."""


class RollbackFailedError(TemporalError):
    """Raised when a rollback operation cannot be completed."""


# ---------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------

class EnvironmentError(ScopedError):
    """Base for environment-related errors."""


class EnvironmentStateError(EnvironmentError):
    """Raised when an environment operation is invalid for its current state."""


class EnvironmentNotFoundError(EnvironmentError):
    """Raised when a referenced environment does not exist."""


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------

class FlowError(ScopedError):
    """Base for flow/stage errors."""


class StageTransitionDeniedError(FlowError):
    """Raised when a stage transition is not permitted."""


class FlowBlockedError(FlowError):
    """Raised when a flow channel blocks the movement of an object."""


class PromotionDeniedError(FlowError):
    """Raised when promotion from environment to scope is not permitted."""


# ---------------------------------------------------------------------------
# Deployments
# ---------------------------------------------------------------------------

class DeploymentError(ScopedError):
    """Base for deployment errors."""


class DeploymentGateFailedError(DeploymentError):
    """Raised when a deployment gate check fails."""


class DeploymentRollbackError(DeploymentError):
    """Raised when a deployment rollback fails."""


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

class SecretError(ScopedError):
    """Base for secret-related errors."""


class SecretNotFoundError(SecretError):
    """Raised when a referenced secret does not exist."""


class SecretAccessDeniedError(SecretError):
    """Raised when a principal lacks permission to access a secret."""


class SecretRefExpiredError(SecretError):
    """Raised when a secret reference has expired or been revoked."""


class SecretRotationError(SecretError):
    """Raised when secret rotation fails."""


class SecretLeakDetectedError(SecretError):
    """Raised when a plaintext secret value is found in a non-secret context."""


# ---------------------------------------------------------------------------
# Integrations & Plugins
# ---------------------------------------------------------------------------

class IntegrationError(ScopedError):
    """Base for integration-related errors."""


class PluginError(ScopedError):
    """Base for plugin-related errors."""


class PluginPermissionError(PluginError):
    """Raised when a plugin attempts an operation it hasn't been granted."""


class PluginSandboxError(PluginError):
    """Raised when a plugin violates its sandbox constraints."""


class HookExecutionError(PluginError):
    """Raised when a plugin hook fails during execution."""


# ---------------------------------------------------------------------------
# Connector & Marketplace
# ---------------------------------------------------------------------------

class ConnectorError(ScopedError):
    """Base for connector-related errors."""


class ConnectorNotApprovedError(ConnectorError):
    """Raised when attempting to use a connector that hasn't been mutually approved."""


class ConnectorRevokedError(ConnectorError):
    """Raised when a connector has been revoked by either side."""


class ConnectorPolicyViolation(ConnectorError):
    """Raised when traffic through a connector violates its policy."""


class FederationError(ConnectorError):
    """Raised when cross-instance communication fails."""


class MarketplaceError(ScopedError):
    """Base for marketplace-related errors."""


class ListingNotFoundError(MarketplaceError):
    """Raised when a marketplace listing doesn't exist."""


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------

class ContractError(ScopedError):
    """Base for contract-related errors."""


class ContractNotFoundError(ContractError):
    """Raised when a referenced contract does not exist."""


class ContractValidationError(ContractError):
    """Raised when data fails contract validation."""


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

class TemplateError(ScopedError):
    """Base for template-related errors."""


class TemplateNotFoundError(TemplateError):
    """Raised when a referenced template does not exist."""


class TemplateVersionNotFoundError(TemplateError):
    """Raised when a referenced template version does not exist."""


class TemplateInstantiationError(TemplateError):
    """Raised when template instantiation fails."""


# ---------------------------------------------------------------------------
# Events & Webhooks
# ---------------------------------------------------------------------------

class EventError(ScopedError):
    """Base for event-related errors."""


class EventNotFoundError(EventError):
    """Raised when a referenced event does not exist."""


class SubscriptionNotFoundError(EventError):
    """Raised when a referenced subscription does not exist."""


class WebhookDeliveryError(EventError):
    """Raised when webhook delivery fails."""


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

class NotificationError(ScopedError):
    """Base for notification-related errors."""


class NotificationNotFoundError(NotificationError):
    """Raised when a referenced notification does not exist."""


# ---------------------------------------------------------------------------
# Scheduling & Jobs
# ---------------------------------------------------------------------------

class SchedulingError(ScopedError):
    """Base for scheduling-related errors."""


class JobNotFoundError(SchedulingError):
    """Raised when a referenced job does not exist."""


class JobStateError(SchedulingError):
    """Raised when a job operation is invalid for its current state."""


# ---------------------------------------------------------------------------
# Storage / Migrations
# ---------------------------------------------------------------------------

class MigrationError(ScopedError):
    """Raised when a schema migration fails."""


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------

class ComplianceError(ScopedError):
    """Base for compliance testing errors."""


class ComplianceViolation(ComplianceError):
    """Raised at runtime when an operation violates framework compliance."""


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

class SyncError(ScopedError):
    """Base for sync-related errors."""


class SyncNotConfiguredError(SyncError):
    """Raised when sync operations are called without an API key."""


class SyncTransportError(SyncError):
    """Raised when HTTP communication with the management plane fails."""


class SyncAuthenticationError(SyncError):
    """Raised when the API key is rejected by the management plane (401/403)."""


class SyncBatchRejectedError(SyncError):
    """Raised when the management plane rejects a sync batch (400)."""


class SyncVerificationError(SyncError):
    """Raised when sync verification detects a chain mismatch."""
