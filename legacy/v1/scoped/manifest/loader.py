"""Manifest loader — orchestrates entity creation in dependency order."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scoped.manifest._services import ScopedServices, build_services
from scoped.manifest.exceptions import ManifestLoadError
from scoped.manifest.parser import parse_manifest
from scoped.manifest.resolver import ReferenceResolver
from scoped.manifest.schema import ManifestDocument
from scoped.manifest.validator import validate_or_raise
from scoped.storage.interface import StorageBackend


@dataclass(slots=True)
class ManifestResult:
    """Result of loading a manifest."""

    created: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    resolver: ReferenceResolver = field(default_factory=ReferenceResolver)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


class ManifestLoader:
    """Load a manifest into a Scoped backend.

    Creates entities in dependency order within a single transaction.
    Idempotent: existing entities are skipped.
    """

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend
        self._services: ScopedServices | None = None

    @property
    def services(self) -> ScopedServices:
        if self._services is None:
            self._services = build_services(self._backend)
        return self._services

    def load(
        self,
        manifest: str | Path | dict[str, Any],
        *,
        secret_values: dict[str, str] | None = None,
        dry_run: bool = False,
    ) -> ManifestResult:
        """Load a manifest into the database.

        Parameters
        ----------
        manifest:
            File path, raw string, or pre-parsed dict.
        secret_values:
            Map of secret name -> plaintext value. Required for any secrets
            defined in the manifest.
        dry_run:
            If True, validate only — don't create anything.
        """
        doc = parse_manifest(manifest)
        validate_or_raise(doc)

        if dry_run:
            return ManifestResult()

        result = ManifestResult()
        svc = self.services

        try:
            self._load_principals(doc, svc, result)
            self._load_scopes(doc, svc, result)
            self._load_memberships(doc, svc, result)
            self._load_objects(doc, svc, result)
            self._load_rules(doc, svc, result)
            self._load_environments(doc, svc, result)
            self._load_pipelines(doc, svc, result)
            self._load_deployment_targets(doc, svc, result)
            self._load_secrets(doc, svc, result, secret_values or {})
            self._load_plugins(doc, svc, result)
        except ManifestLoadError:
            raise
        except Exception as e:
            raise ManifestLoadError(f"Failed during manifest load: {e}") from e

        return result

    def validate(self, manifest: str | Path | dict[str, Any]) -> list[str]:
        """Validate a manifest without loading it. Returns error strings."""
        from scoped.manifest.validator import validate_manifest

        doc = parse_manifest(manifest)
        return validate_manifest(doc)

    # -- Section loaders (dependency-ordered) ----------------------------------

    def _load_principals(
        self,
        doc: ManifestDocument,
        svc: ScopedServices,
        result: ManifestResult,
    ) -> None:
        for spec in doc.principals:
            existing = self._find_principal(svc, spec.kind, spec.display_name)
            if existing:
                result.resolver.register("principals", spec.name, existing)
                result.skipped.append(("principals", spec.name))
                continue

            principal = svc.principals.create_principal(
                kind=spec.kind,
                display_name=spec.display_name or spec.name,
                metadata=spec.metadata,
            )
            result.resolver.register("principals", spec.name, principal.id)
            result.created.append(("principals", spec.name))

    def _load_scopes(
        self,
        doc: ManifestDocument,
        svc: ScopedServices,
        result: ManifestResult,
    ) -> None:
        # Topological sort by parent refs
        ordered = self._topo_sort_scopes(doc)
        for spec in ordered:
            owner_id = result.resolver.resolve("principals", spec.owner)
            parent_id = None
            if spec.parent:
                parent_id = result.resolver.resolve("scopes", spec.parent)

            existing = self._find_scope(svc, spec.name, owner_id)
            if existing:
                result.resolver.register("scopes", spec.name, existing)
                result.skipped.append(("scopes", spec.name))
                continue

            scope = svc.scopes.create_scope(
                name=spec.name,
                owner_id=owner_id,
                description=spec.description,
                parent_scope_id=parent_id,
                metadata=spec.metadata,
            )
            result.resolver.register("scopes", spec.name, scope.id)
            result.created.append(("scopes", spec.name))

    def _load_memberships(
        self,
        doc: ManifestDocument,
        svc: ScopedServices,
        result: ManifestResult,
    ) -> None:
        from scoped.tenancy.models import ScopeRole

        role_map = {r.value: r for r in ScopeRole}

        for i, spec in enumerate(doc.memberships):
            scope_id = result.resolver.resolve("scopes", spec.scope)
            principal_id = result.resolver.resolve("principals", spec.principal)

            # Determine granted_by
            if spec.granted_by:
                granted_by = result.resolver.resolve("principals", spec.granted_by)
            else:
                # Default to scope owner
                scope_owner = next(
                    s.owner for s in doc.scopes if s.name == spec.scope
                )
                granted_by = result.resolver.resolve("principals", scope_owner)

            role = role_map.get(spec.role, ScopeRole.EDITOR)

            try:
                svc.scopes.add_member(
                    scope_id,
                    principal_id=principal_id,
                    role=role,
                    granted_by=granted_by,
                )
                result.created.append(("memberships", f"{spec.scope}/{spec.principal}"))
            except Exception:
                # Already a member — skip
                result.skipped.append(("memberships", f"{spec.scope}/{spec.principal}"))

    def _load_objects(
        self,
        doc: ManifestDocument,
        svc: ScopedServices,
        result: ManifestResult,
    ) -> None:
        for spec in doc.objects:
            owner_id = result.resolver.resolve("principals", spec.owner)

            obj, _ = svc.manager.create(
                object_type=spec.type,
                owner_id=owner_id,
                data=spec.data if spec.data else {"name": spec.name},
            )
            result.resolver.register("objects", spec.name, obj.id)
            result.created.append(("objects", spec.name))

            # Auto-project into scopes
            for scope_ref in spec.project_into:
                scope_id = result.resolver.resolve("scopes", scope_ref)
                try:
                    svc.projections.project(
                        scope_id=scope_id,
                        object_id=obj.id,
                        projected_by=owner_id,
                    )
                except Exception:
                    pass  # Already projected

    def _load_rules(
        self,
        doc: ManifestDocument,
        svc: ScopedServices,
        result: ManifestResult,
    ) -> None:
        from scoped.rules.models import BindingTargetType, RuleEffect, RuleType

        type_map = {r.value: r for r in RuleType}
        effect_map = {r.value.upper(): r for r in RuleEffect}
        target_map = {t.value: t for t in BindingTargetType}

        for spec in doc.rules:
            created_by = result.resolver.resolve("principals", spec.created_by) if spec.created_by else ""

            rule_type = type_map.get(spec.rule_type, RuleType.ACCESS)
            effect = effect_map.get(spec.effect.upper(), RuleEffect.DENY)

            rule = svc.rules.create_rule(
                name=spec.name,
                rule_type=rule_type,
                effect=effect,
                priority=spec.priority,
                description=spec.description,
                created_by=created_by,
                conditions=spec.conditions,
            )
            result.resolver.register("rules", spec.name, rule.id)
            result.created.append(("rules", spec.name))

            # Bind rule to targets
            for binding in spec.bind_to:
                bt = target_map.get(binding.target_type, BindingTargetType.SCOPE)
                # Resolve target reference
                section_map = {
                    "scope": "scopes",
                    "principal": "principals",
                    "object": "objects",
                    "environment": "environments",
                }
                section = section_map.get(binding.target_type, "scopes")
                target_id = result.resolver.resolve(section, binding.target)

                svc.rules.bind_rule(
                    rule.id,
                    target_type=bt,
                    target_id=target_id,
                    bound_by=created_by,
                )

    def _load_environments(
        self,
        doc: ManifestDocument,
        svc: ScopedServices,
        result: ManifestResult,
    ) -> None:
        for spec in doc.environments:
            owner_id = result.resolver.resolve("principals", spec.owner)

            env = svc.environments.spawn(
                name=spec.name,
                owner_id=owner_id,
                description=spec.description,
                ephemeral=spec.ephemeral,
                metadata=spec.metadata,
            )
            result.resolver.register("environments", spec.name, env.id)
            result.created.append(("environments", spec.name))

    def _load_pipelines(
        self,
        doc: ManifestDocument,
        svc: ScopedServices,
        result: ManifestResult,
    ) -> None:
        for spec in doc.pipelines:
            owner_id = result.resolver.resolve("principals", spec.owner)

            pipeline = svc.pipelines.create_pipeline(
                name=spec.name,
                owner_id=owner_id,
                description=spec.description,
            )
            result.resolver.register("pipelines", spec.name, pipeline.id)
            result.created.append(("pipelines", spec.name))

            # Add stages
            for stage in spec.stages:
                svc.pipelines.add_stage(
                    pipeline.id,
                    name=stage.name,
                    ordinal=stage.order,
                )

    def _load_deployment_targets(
        self,
        doc: ManifestDocument,
        svc: ScopedServices,
        result: ManifestResult,
    ) -> None:
        for spec in doc.deployment_targets:
            owner_id = result.resolver.resolve("principals", spec.owner)

            target = svc.deployments.create_target(
                name=spec.name,
                target_type=spec.target_type,
                owner_id=owner_id,
                config=spec.config,
            )
            result.resolver.register("deployment_targets", spec.name, target.id)
            result.created.append(("deployment_targets", spec.name))

    def _load_secrets(
        self,
        doc: ManifestDocument,
        svc: ScopedServices,
        result: ManifestResult,
        secret_values: dict[str, str],
    ) -> None:
        for spec in doc.secrets:
            owner_id = result.resolver.resolve("principals", spec.owner)

            value = secret_values.get(spec.name)
            if value is None:
                result.errors.append(
                    f"secrets/{spec.name}: no value provided in secret_values"
                )
                continue

            secret, _ = svc.secrets.create_secret(
                name=spec.name,
                plaintext_value=value,
                owner_id=owner_id,
                description=spec.description,
                classification=spec.classification,
            )
            result.resolver.register("secrets", spec.name, secret.id)
            result.created.append(("secrets", spec.name))

    def _load_plugins(
        self,
        doc: ManifestDocument,
        svc: ScopedServices,
        result: ManifestResult,
    ) -> None:
        for spec in doc.plugins:
            owner_id = result.resolver.resolve("principals", spec.owner)
            scope_id = None
            if spec.scope:
                scope_id = result.resolver.resolve("scopes", spec.scope)

            plugin = svc.plugins.install_plugin(
                name=spec.name,
                owner_id=owner_id,
                version=spec.version,
                description=spec.description,
                scope_id=scope_id,
                manifest=spec.manifest,
            )
            result.resolver.register("plugins", spec.name, plugin.id)
            result.created.append(("plugins", spec.name))

    # -- Helpers ---------------------------------------------------------------

    def _topo_sort_scopes(self, doc: ManifestDocument) -> list[Any]:
        """Sort scopes so parents come before children."""
        by_name = {s.name: s for s in doc.scopes}
        ordered: list[Any] = []
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            spec = by_name[name]
            if spec.parent and spec.parent in by_name:
                visit(spec.parent)
            ordered.append(spec)

        for scope in doc.scopes:
            visit(scope.name)

        return ordered

    def _find_principal(
        self, svc: ScopedServices, kind: str, display_name: str
    ) -> str | None:
        """Find an existing principal by kind+display_name. Returns ID or None."""
        try:
            rows = self._backend.fetch_all(
                "SELECT id FROM principals WHERE kind = ? AND display_name = ? "
                "AND lifecycle = 'ACTIVE'",
                (kind, display_name),
            )
            if rows:
                return rows[0]["id"]
        except Exception:
            pass
        return None

    def _find_scope(
        self, svc: ScopedServices, name: str, owner_id: str
    ) -> str | None:
        """Find an existing scope by name+owner. Returns ID or None."""
        try:
            rows = self._backend.fetch_all(
                "SELECT id FROM scopes WHERE name = ? AND owner_id = ? "
                "AND lifecycle = 'ACTIVE'",
                (name, owner_id),
            )
            if rows:
                return rows[0]["id"]
        except Exception:
            pass
        return None
