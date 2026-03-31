"""Core type definitions shared across all layers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

def generate_id() -> str:
    """Generate a globally unique identifier."""
    return uuid.uuid4().hex


def now_utc() -> datetime:
    """Current UTC timestamp."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# URN — Universal Resource Name for every registered construct
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class URN:
    """
    Globally unique name for any registered construct.

    Format: scoped:<kind>:<namespace>:<name>:<version>
    Example: scoped:model:myapp:User:1
    """
    kind: str
    namespace: str
    name: str
    version: int = 1

    def __str__(self) -> str:
        return f"scoped:{self.kind}:{self.namespace}:{self.name}:{self.version}"

    @classmethod
    def parse(cls, raw: str) -> URN:
        parts = raw.split(":")
        # Format: scoped:<kind>:<namespace>:<name>:<version>
        # The name field may itself contain colons (e.g. "user:abc123"),
        # so we require at least 5 parts and join the middle segments.
        if len(parts) < 5 or parts[0] != "scoped":
            raise ValueError(f"Invalid URN: {raw}")
        kind = parts[1]
        namespace = parts[2]
        version = int(parts[-1])
        name = ":".join(parts[3:-1])
        return cls(kind=kind, namespace=namespace, name=name, version=version)


# ---------------------------------------------------------------------------
# Lifecycle states
# ---------------------------------------------------------------------------

class Lifecycle(Enum):
    DRAFT = auto()
    ACTIVE = auto()
    DEPRECATED = auto()
    ARCHIVED = auto()


# ---------------------------------------------------------------------------
# Action types — every traceable action falls into one of these
# ---------------------------------------------------------------------------

class ActionType(Enum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    SHARE = "share"
    REVOKE = "revoke"
    REGISTER = "register"
    UNREGISTER = "unregister"
    RULE_CHANGE = "rule_change"
    ROLLBACK = "rollback"
    SCOPE_CREATE = "scope_create"
    SCOPE_MODIFY = "scope_modify"
    SCOPE_DISSOLVE = "scope_dissolve"
    MEMBERSHIP_CHANGE = "membership_change"
    OWNERSHIP_TRANSFER = "ownership_transfer"
    LIFECYCLE_CHANGE = "lifecycle_change"
    ACCESS_CHECK = "access_check"
    PROJECTION = "projection"
    # Environments
    ENV_SPAWN = "env_spawn"
    ENV_SUSPEND = "env_suspend"
    ENV_RESUME = "env_resume"
    ENV_COMPLETE = "env_complete"
    ENV_DISCARD = "env_discard"
    ENV_PROMOTE = "env_promote"
    ENV_SNAPSHOT = "env_snapshot"
    # Flow
    STAGE_TRANSITION = "stage_transition"
    FLOW_PUSH = "flow_push"
    PROMOTION = "promotion"
    # Deployments
    DEPLOY = "deploy"
    DEPLOY_ROLLBACK = "deploy_rollback"
    GATE_CHECK = "gate_check"
    # Secrets
    SECRET_CREATE = "secret_create"
    SECRET_READ = "secret_read"
    SECRET_ROTATE = "secret_rotate"
    SECRET_REVOKE = "secret_revoke"
    SECRET_REF_GRANT = "secret_ref_grant"
    SECRET_REF_RESOLVE = "secret_ref_resolve"
    # Integrations & Plugins
    INTEGRATION_CONNECT = "integration_connect"
    INTEGRATION_DISCONNECT = "integration_disconnect"
    PLUGIN_INSTALL = "plugin_install"
    PLUGIN_ACTIVATE = "plugin_activate"
    PLUGIN_SUSPEND = "plugin_suspend"
    PLUGIN_UNINSTALL = "plugin_uninstall"
    HOOK_EXECUTE = "hook_execute"
    # Connector & Marketplace
    CONNECTOR_PROPOSE = "connector_propose"
    CONNECTOR_APPROVE = "connector_approve"
    CONNECTOR_REVOKE = "connector_revoke"
    CONNECTOR_SYNC = "connector_sync"
    MARKETPLACE_PUBLISH = "marketplace_publish"
    MARKETPLACE_INSTALL = "marketplace_install"
    # Contracts
    CONTRACT_CREATE = "contract_create"
    CONTRACT_UPDATE = "contract_update"
    CONTRACT_VALIDATE = "contract_validate"
    # Blobs
    BLOB_CREATE = "blob_create"
    BLOB_READ = "blob_read"
    BLOB_DELETE = "blob_delete"
    # Rule extensions
    RATE_LIMIT_CHECK = "rate_limit_check"
    QUOTA_CHECK = "quota_check"
    FEATURE_FLAG_CHECK = "feature_flag_check"
    REDACTION_APPLY = "redaction_apply"
    # Configuration
    CONFIG_SET = "config_set"
    CONFIG_DELETE = "config_delete"
    # Templates
    TEMPLATE_CREATE = "template_create"
    TEMPLATE_UPDATE = "template_update"
    TEMPLATE_INSTANTIATE = "template_instantiate"
    # Import / Export
    EXPORT = "export"
    IMPORT = "import"
    # Events & Webhooks
    EVENT_EMIT = "event_emit"
    EVENT_SUBSCRIBE = "event_subscribe"
    EVENT_UNSUBSCRIBE = "event_unsubscribe"
    WEBHOOK_CREATE = "webhook_create"
    WEBHOOK_DELETE = "webhook_delete"
    WEBHOOK_DELIVER = "webhook_deliver"
    # Notifications
    NOTIFICATION_CREATE = "notification_create"
    NOTIFICATION_READ = "notification_read"
    NOTIFICATION_DISMISS = "notification_dismiss"
    NOTIFICATION_RULE_CREATE = "notification_rule_create"
    # Scheduling & Jobs
    SCHEDULE_CREATE = "schedule_create"
    SCHEDULE_ARCHIVE = "schedule_archive"
    JOB_ENQUEUE = "job_enqueue"
    JOB_START = "job_start"
    JOB_COMPLETE = "job_complete"
    JOB_FAIL = "job_fail"
    JOB_CANCEL = "job_cancel"


# ---------------------------------------------------------------------------
# Protocols — interfaces that other layers implement
# ---------------------------------------------------------------------------

@runtime_checkable
class Identifiable(Protocol):
    """Anything that has a unique id."""
    @property
    def id(self) -> str: ...


@runtime_checkable
class Versioned(Protocol):
    """Anything that tracks versions."""
    @property
    def version(self) -> int: ...


@runtime_checkable
class Owned(Protocol):
    """Anything that has an owner principal."""
    @property
    def owner_id(self) -> str: ...


# ---------------------------------------------------------------------------
# Generic metadata bag
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Metadata:
    """Arbitrary key-value metadata attached to any framework entity."""
    data: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def merge(self, other: dict[str, Any]) -> None:
        self.data.update(other)

    def snapshot(self) -> dict[str, Any]:
        """Return an immutable copy for versioning."""
        import copy
        return copy.deepcopy(self.data)
