"""Pre-execution validation of manifest references."""

from __future__ import annotations

from scoped.manifest.exceptions import ManifestValidationError
from scoped.manifest.schema import ManifestDocument


def validate_manifest(doc: ManifestDocument) -> list[str]:
    """Validate a parsed manifest for reference integrity.

    Returns a list of error strings. Empty list means valid.
    Raises ManifestValidationError if errors are found (convenience).
    """
    errors: list[str] = []

    # Build name sets per section
    principal_names = _check_duplicates(
        [p.name for p in doc.principals], "principals", errors
    )
    scope_names = _check_duplicates(
        [s.name for s in doc.scopes], "scopes", errors
    )
    object_names = _check_duplicates(
        [o.name for o in doc.objects], "objects", errors
    )
    rule_names = _check_duplicates(
        [r.name for r in doc.rules], "rules", errors
    )
    env_names = _check_duplicates(
        [e.name for e in doc.environments], "environments", errors
    )
    pipeline_names = _check_duplicates(
        [p.name for p in doc.pipelines], "pipelines", errors
    )
    target_names = _check_duplicates(
        [t.name for t in doc.deployment_targets], "deployment_targets", errors
    )
    secret_names = _check_duplicates(
        [s.name for s in doc.secrets], "secrets", errors
    )
    plugin_names = _check_duplicates(
        [p.name for p in doc.plugins], "plugins", errors
    )

    # Reference checks: scopes
    for scope in doc.scopes:
        if scope.owner not in principal_names:
            errors.append(f"scopes/{scope.name}: owner '{scope.owner}' not in principals")
        if scope.parent is not None and scope.parent not in scope_names:
            errors.append(
                f"scopes/{scope.name}: parent '{scope.parent}' not in scopes"
            )

    # Check for scope parent cycles
    _check_scope_cycles(doc, errors)

    # Reference checks: memberships
    for i, mem in enumerate(doc.memberships):
        if mem.scope not in scope_names:
            errors.append(f"memberships[{i}]: scope '{mem.scope}' not in scopes")
        if mem.principal not in principal_names:
            errors.append(
                f"memberships[{i}]: principal '{mem.principal}' not in principals"
            )
        if mem.granted_by and mem.granted_by not in principal_names:
            errors.append(
                f"memberships[{i}]: granted_by '{mem.granted_by}' not in principals"
            )

    # Reference checks: objects
    for obj in doc.objects:
        if obj.owner not in principal_names:
            errors.append(f"objects/{obj.name}: owner '{obj.owner}' not in principals")
        for scope_ref in obj.project_into:
            if scope_ref not in scope_names:
                errors.append(
                    f"objects/{obj.name}: project_into scope '{scope_ref}' not in scopes"
                )

    # Reference checks: rules
    for rule in doc.rules:
        if rule.created_by and rule.created_by not in principal_names:
            errors.append(
                f"rules/{rule.name}: created_by '{rule.created_by}' not in principals"
            )
        for binding in rule.bind_to:
            _ref = binding.target
            if binding.target_type == "scope" and _ref not in scope_names:
                errors.append(
                    f"rules/{rule.name}: bind_to scope '{_ref}' not in scopes"
                )
            elif binding.target_type == "principal" and _ref not in principal_names:
                errors.append(
                    f"rules/{rule.name}: bind_to principal '{_ref}' not in principals"
                )
            elif binding.target_type == "object" and _ref not in object_names:
                errors.append(
                    f"rules/{rule.name}: bind_to object '{_ref}' not in objects"
                )

    # Reference checks: environments
    for env in doc.environments:
        if env.owner not in principal_names:
            errors.append(
                f"environments/{env.name}: owner '{env.owner}' not in principals"
            )

    # Reference checks: pipelines
    for pipe in doc.pipelines:
        if pipe.owner not in principal_names:
            errors.append(
                f"pipelines/{pipe.name}: owner '{pipe.owner}' not in principals"
            )

    # Reference checks: deployment_targets
    for target in doc.deployment_targets:
        if target.owner not in principal_names:
            errors.append(
                f"deployment_targets/{target.name}: owner '{target.owner}' not in principals"
            )

    # Reference checks: secrets
    for secret in doc.secrets:
        if secret.owner not in principal_names:
            errors.append(
                f"secrets/{secret.name}: owner '{secret.owner}' not in principals"
            )

    # Reference checks: plugins
    for plugin in doc.plugins:
        if plugin.owner not in principal_names:
            errors.append(
                f"plugins/{plugin.name}: owner '{plugin.owner}' not in principals"
            )
        if plugin.scope is not None and plugin.scope not in scope_names:
            errors.append(
                f"plugins/{plugin.name}: scope '{plugin.scope}' not in scopes"
            )

    return errors


def validate_or_raise(doc: ManifestDocument) -> None:
    """Validate and raise ManifestValidationError if any errors found."""
    errors = validate_manifest(doc)
    if errors:
        raise ManifestValidationError(errors)


def _check_duplicates(
    names: list[str], section: str, errors: list[str]
) -> set[str]:
    """Check for duplicate names within a section. Returns the name set."""
    seen: set[str] = set()
    for name in names:
        if name in seen:
            errors.append(f"{section}: duplicate name '{name}'")
        seen.add(name)
    return seen


def _check_scope_cycles(doc: ManifestDocument, errors: list[str]) -> None:
    """Detect cycles in scope parent references."""
    parent_map = {s.name: s.parent for s in doc.scopes if s.parent is not None}

    for name in parent_map:
        visited: set[str] = set()
        current: str | None = name
        while current is not None:
            if current in visited:
                errors.append(f"scopes: cycle detected involving '{current}'")
                break
            visited.add(current)
            current = parent_map.get(current)
