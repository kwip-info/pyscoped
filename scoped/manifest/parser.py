"""Parse YAML/JSON into a ManifestDocument."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scoped.manifest.exceptions import ManifestParseError
from scoped.manifest.schema import (
    DeploymentTargetSpec,
    EnvironmentSpec,
    ManifestDocument,
    MembershipSpec,
    ObjectSpec,
    PipelineSpec,
    PipelineStageSpec,
    PluginSpec,
    PrincipalSpec,
    RuleBindingSpec,
    RuleSpec,
    ScopeSpec,
    SecretSpec,
)


def parse_manifest(source: str | Path | dict[str, Any]) -> ManifestDocument:
    """Parse a manifest from a file path, raw string, or pre-loaded dict.

    Accepts:
      - A dict (already parsed)
      - A Path or string path to a .yaml/.yml/.json file
      - A raw JSON or YAML string
    """
    raw = _load_raw(source)
    return _build_document(raw)


def _load_raw(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Load raw dict from the source."""
    if isinstance(source, dict):
        return source

    # If it looks like a file path (not empty, no newlines, reasonable length), try it
    if isinstance(source, Path) or (
        isinstance(source, str)
        and source.strip()
        and "\n" not in source
        and len(source) < 1024
    ):
        path = Path(source) if not isinstance(source, Path) else source
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            return _parse_text(text, path.suffix)

    # Otherwise treat as raw string
    if isinstance(source, str):
        return _parse_text(source, "")

    raise ManifestParseError(f"Cannot load manifest from: {source!r}")


def _parse_text(text: str, suffix: str) -> dict[str, Any]:
    """Parse text as JSON or YAML."""
    text = text.strip()
    if not text:
        raise ManifestParseError("Empty manifest")

    # Try JSON first (always available)
    if text.startswith("{") or suffix == ".json":
        try:
            result = json.loads(text)
            if not isinstance(result, dict):
                raise ManifestParseError("Manifest must be a JSON object")
            return result
        except json.JSONDecodeError as e:
            if suffix == ".json":
                raise ManifestParseError(f"Invalid JSON: {e}") from e

    # Try YAML (optional dependency)
    if suffix in (".yaml", ".yml", ""):
        try:
            import yaml  # type: ignore[import-untyped]

            result = yaml.safe_load(text)
            if not isinstance(result, dict):
                raise ManifestParseError("Manifest must be a YAML mapping")
            return result
        except ImportError:
            if suffix in (".yaml", ".yml"):
                raise ManifestParseError(
                    "PyYAML is required for YAML manifests: pip install scoped[manifest]"
                )
        except Exception as e:
            raise ManifestParseError(f"Invalid YAML: {e}") from e

    raise ManifestParseError("Could not parse manifest as JSON or YAML")


def _build_document(raw: dict[str, Any]) -> ManifestDocument:
    """Convert a raw dict into a ManifestDocument."""
    # Support both top-level and nested under "scoped" key
    if "scoped" in raw and isinstance(raw["scoped"], dict):
        data = raw["scoped"]
    else:
        data = raw

    version = str(data.get("version", "1.0"))
    namespace = str(data.get("namespace", "default"))

    return ManifestDocument(
        version=version,
        namespace=namespace,
        principals=_parse_principals(data.get("principals", [])),
        scopes=_parse_scopes(data.get("scopes", [])),
        memberships=_parse_memberships(data.get("memberships", [])),
        objects=_parse_objects(data.get("objects", [])),
        rules=_parse_rules(data.get("rules", [])),
        environments=_parse_environments(data.get("environments", [])),
        pipelines=_parse_pipelines(data.get("pipelines", [])),
        deployment_targets=_parse_deployment_targets(
            data.get("deployment_targets", [])
        ),
        secrets=_parse_secrets(data.get("secrets", [])),
        plugins=_parse_plugins(data.get("plugins", [])),
    )


def _parse_principals(items: list[dict[str, Any]]) -> list[PrincipalSpec]:
    specs = []
    for item in items:
        _require(item, "name", "principals")
        specs.append(
            PrincipalSpec(
                name=item["name"],
                kind=item.get("kind", "user"),
                display_name=item.get("display_name", item["name"]),
                metadata=item.get("metadata"),
            )
        )
    return specs


def _parse_scopes(items: list[dict[str, Any]]) -> list[ScopeSpec]:
    specs = []
    for item in items:
        _require(item, "name", "scopes")
        _require(item, "owner", "scopes")
        specs.append(
            ScopeSpec(
                name=item["name"],
                owner=item["owner"],
                description=item.get("description", ""),
                parent=item.get("parent"),
                metadata=item.get("metadata"),
            )
        )
    return specs


def _parse_memberships(items: list[dict[str, Any]]) -> list[MembershipSpec]:
    specs = []
    for item in items:
        _require(item, "scope", "memberships")
        _require(item, "principal", "memberships")
        specs.append(
            MembershipSpec(
                scope=item["scope"],
                principal=item["principal"],
                role=item.get("role", "editor"),
                granted_by=item.get("granted_by", ""),
            )
        )
    return specs


def _parse_objects(items: list[dict[str, Any]]) -> list[ObjectSpec]:
    specs = []
    for item in items:
        _require(item, "name", "objects")
        _require(item, "type", "objects")
        _require(item, "owner", "objects")
        project_into = item.get("project_into", [])
        if isinstance(project_into, list):
            # Handle both list of strings and list of dicts with "scope" key
            resolved = []
            for p in project_into:
                if isinstance(p, dict):
                    resolved.append(p.get("scope", ""))
                else:
                    resolved.append(str(p))
            project_into = resolved
        specs.append(
            ObjectSpec(
                name=item["name"],
                type=item["type"],
                owner=item["owner"],
                data=item.get("data", {}),
                project_into=project_into,
            )
        )
    return specs


def _parse_rules(items: list[dict[str, Any]]) -> list[RuleSpec]:
    specs = []
    for item in items:
        _require(item, "name", "rules")
        _require(item, "rule_type", "rules")
        _require(item, "effect", "rules")
        bind_to = []
        for b in item.get("bind_to", []):
            bind_to.append(
                RuleBindingSpec(
                    target_type=b.get("target_type", "scope"),
                    target=b.get("target", ""),
                )
            )
        specs.append(
            RuleSpec(
                name=item["name"],
                rule_type=item["rule_type"],
                effect=item["effect"],
                priority=item.get("priority", 0),
                description=item.get("description", ""),
                created_by=item.get("created_by", ""),
                conditions=item.get("conditions"),
                bind_to=bind_to,
            )
        )
    return specs


def _parse_environments(items: list[dict[str, Any]]) -> list[EnvironmentSpec]:
    specs = []
    for item in items:
        _require(item, "name", "environments")
        _require(item, "owner", "environments")
        specs.append(
            EnvironmentSpec(
                name=item["name"],
                owner=item["owner"],
                description=item.get("description", ""),
                ephemeral=item.get("ephemeral", True),
                metadata=item.get("metadata"),
            )
        )
    return specs


def _parse_pipelines(items: list[dict[str, Any]]) -> list[PipelineSpec]:
    specs = []
    for item in items:
        _require(item, "name", "pipelines")
        _require(item, "owner", "pipelines")
        stages = []
        for i, s in enumerate(item.get("stages", [])):
            if isinstance(s, str):
                stages.append(PipelineStageSpec(name=s, order=i))
            else:
                stages.append(
                    PipelineStageSpec(name=s["name"], order=s.get("order", i))
                )
        specs.append(
            PipelineSpec(
                name=item["name"],
                owner=item["owner"],
                description=item.get("description", ""),
                stages=stages,
            )
        )
    return specs


def _parse_deployment_targets(
    items: list[dict[str, Any]],
) -> list[DeploymentTargetSpec]:
    specs = []
    for item in items:
        _require(item, "name", "deployment_targets")
        _require(item, "target_type", "deployment_targets")
        _require(item, "owner", "deployment_targets")
        specs.append(
            DeploymentTargetSpec(
                name=item["name"],
                target_type=item["target_type"],
                owner=item["owner"],
                config=item.get("config"),
            )
        )
    return specs


def _parse_secrets(items: list[dict[str, Any]]) -> list[SecretSpec]:
    specs = []
    for item in items:
        _require(item, "name", "secrets")
        _require(item, "owner", "secrets")
        specs.append(
            SecretSpec(
                name=item["name"],
                owner=item["owner"],
                description=item.get("description", ""),
                classification=item.get("classification", "standard"),
            )
        )
    return specs


def _parse_plugins(items: list[dict[str, Any]]) -> list[PluginSpec]:
    specs = []
    for item in items:
        _require(item, "name", "plugins")
        _require(item, "owner", "plugins")
        specs.append(
            PluginSpec(
                name=item["name"],
                owner=item["owner"],
                version=item.get("version", "0.1.0"),
                description=item.get("description", ""),
                scope=item.get("scope"),
                manifest=item.get("manifest"),
            )
        )
    return specs


def _require(item: dict[str, Any], key: str, section: str) -> None:
    """Raise ManifestParseError if a required key is missing."""
    if key not in item:
        raise ManifestParseError(
            f"Missing required field '{key}' in {section} entry: {item}"
        )
