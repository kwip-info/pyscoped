"""Dataclasses representing the manifest document structure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class PrincipalSpec:
    """A principal to create."""

    name: str
    kind: str = "user"
    display_name: str = ""
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ScopeSpec:
    """A scope to create."""

    name: str
    owner: str  # ref to principal name
    description: str = ""
    parent: str | None = None  # ref to scope name
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class MembershipSpec:
    """A scope membership to create."""

    scope: str  # ref to scope name
    principal: str  # ref to principal name
    role: str = "editor"
    granted_by: str = ""  # ref to principal name; defaults to scope owner


@dataclass(frozen=True, slots=True)
class ObjectSpec:
    """An object to create."""

    name: str
    type: str
    owner: str  # ref to principal name
    data: dict[str, Any] = field(default_factory=dict)
    project_into: list[str] = field(default_factory=list)  # refs to scope names


@dataclass(frozen=True, slots=True)
class RuleBindingSpec:
    """A rule binding target."""

    target_type: str  # "scope", "principal", "object"
    target: str  # ref name in the corresponding section


@dataclass(frozen=True, slots=True)
class RuleSpec:
    """A rule to create."""

    name: str
    rule_type: str  # "access", "lifecycle", "visibility"
    effect: str  # "allow", "deny"
    priority: int = 0
    description: str = ""
    created_by: str = ""  # ref to principal name
    conditions: dict[str, Any] | None = None
    bind_to: list[RuleBindingSpec] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class EnvironmentSpec:
    """An environment to create."""

    name: str
    owner: str  # ref to principal name
    description: str = ""
    ephemeral: bool = True
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PipelineStageSpec:
    """A stage within a pipeline."""

    name: str
    order: int


@dataclass(frozen=True, slots=True)
class PipelineSpec:
    """A pipeline to create."""

    name: str
    owner: str  # ref to principal name
    description: str = ""
    stages: list[PipelineStageSpec] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DeploymentTargetSpec:
    """A deployment target to create."""

    name: str
    target_type: str
    owner: str  # ref to principal name
    config: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SecretSpec:
    """A secret to create. Values come from secret_values param, NOT the manifest."""

    name: str
    owner: str  # ref to principal name
    description: str = ""
    classification: str = "standard"


@dataclass(frozen=True, slots=True)
class PluginSpec:
    """A plugin to install."""

    name: str
    owner: str  # ref to principal name
    version: str = "0.1.0"
    description: str = ""
    scope: str | None = None  # ref to scope name
    manifest: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ManifestDocument:
    """Complete parsed manifest."""

    version: str = "1.0"
    namespace: str = "default"
    principals: list[PrincipalSpec] = field(default_factory=list)
    scopes: list[ScopeSpec] = field(default_factory=list)
    memberships: list[MembershipSpec] = field(default_factory=list)
    objects: list[ObjectSpec] = field(default_factory=list)
    rules: list[RuleSpec] = field(default_factory=list)
    environments: list[EnvironmentSpec] = field(default_factory=list)
    pipelines: list[PipelineSpec] = field(default_factory=list)
    deployment_targets: list[DeploymentTargetSpec] = field(default_factory=list)
    secrets: list[SecretSpec] = field(default_factory=list)
    plugins: list[PluginSpec] = field(default_factory=list)
