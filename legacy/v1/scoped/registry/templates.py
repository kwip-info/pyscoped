"""General-purpose templates for any registered construct type.

Templates are reusable blueprints that can be instantiated with overrides
to create concrete constructs. Environment templates, scope templates,
pipeline templates, rule set templates, object templates — all go through
this system.

Templates are versioned, scoped, and audited like any registered construct.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from scoped.exceptions import (
    AccessDeniedError,
    TemplateInstantiationError,
    TemplateNotFoundError,
    TemplateVersionNotFoundError,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import template_versions, templates
from scoped.storage.interface import StorageBackend
from scoped.types import Lifecycle, generate_id, now_utc


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Template:
    """A reusable blueprint for creating constructs."""

    id: str
    name: str
    description: str
    template_type: str          # what this creates: "scope", "environment", "object", "pipeline", "rule_set", etc.
    owner_id: str
    schema: dict[str, Any]      # the blueprint — default values, structure
    current_version: int
    created_at: datetime
    scope_id: str | None        # optional scope containment
    lifecycle: Lifecycle

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "template_type": self.template_type,
            "owner_id": self.owner_id,
            "schema": self.schema,
            "current_version": self.current_version,
            "created_at": self.created_at.isoformat(),
            "scope_id": self.scope_id,
            "lifecycle": self.lifecycle.name,
        }


@dataclass(frozen=True, slots=True)
class TemplateVersion:
    """An immutable snapshot of a template at a specific version."""

    id: str
    template_id: str
    version: int
    schema: dict[str, Any]
    created_at: datetime
    created_by: str
    change_reason: str


@dataclass(frozen=True, slots=True)
class InstantiationResult:
    """The result of instantiating a template."""

    template_id: str
    template_name: str
    template_type: str
    template_version: int
    data: dict[str, Any]            # merged result (defaults + overrides)
    overrides_applied: dict[str, Any]  # what was overridden


# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------

def template_from_row(row: dict[str, Any]) -> Template:
    """Convert a database row to a Template."""
    return Template(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        template_type=row["template_type"],
        owner_id=row["owner_id"],
        schema=json.loads(row["schema_json"]),
        current_version=row["current_version"],
        created_at=datetime.fromisoformat(row["created_at"]),
        scope_id=row.get("scope_id"),
        lifecycle=Lifecycle[row["lifecycle"]],
    )


def version_from_row(row: dict[str, Any]) -> TemplateVersion:
    """Convert a database row to a TemplateVersion."""
    return TemplateVersion(
        id=row["id"],
        template_id=row["template_id"],
        version=row["version"],
        schema=json.loads(row["schema_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        created_by=row["created_by"],
        change_reason=row["change_reason"],
    )


# ---------------------------------------------------------------------------
# TemplateStore — CRUD for templates
# ---------------------------------------------------------------------------

class TemplateStore:
    """Manages template persistence and versioning."""

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_template(
        self,
        *,
        name: str,
        template_type: str,
        owner_id: str,
        schema: dict[str, Any],
        description: str = "",
        scope_id: str | None = None,
    ) -> Template:
        """Create a new template with initial version."""
        ts = now_utc()
        template_id = generate_id()
        version_id = generate_id()

        stmt = sa.insert(templates).values(
            id=template_id,
            name=name,
            description=description,
            template_type=template_type,
            owner_id=owner_id,
            schema_json=json.dumps(schema),
            current_version=1,
            created_at=ts.isoformat(),
            scope_id=scope_id,
            lifecycle=Lifecycle.ACTIVE.name,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        stmt = sa.insert(template_versions).values(
            id=version_id,
            template_id=template_id,
            version=1,
            schema_json=json.dumps(schema),
            created_at=ts.isoformat(),
            created_by=owner_id,
            change_reason="initial",
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        return Template(
            id=template_id,
            name=name,
            description=description,
            template_type=template_type,
            owner_id=owner_id,
            schema=schema,
            current_version=1,
            created_at=ts,
            scope_id=scope_id,
            lifecycle=Lifecycle.ACTIVE,
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_template(self, template_id: str) -> Template:
        """Get a template by ID. Raises TemplateNotFoundError if not found."""
        stmt = sa.select(templates).where(templates.c.id == template_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            raise TemplateNotFoundError(
                f"Template not found: {template_id}",
                context={"template_id": template_id},
            )
        return template_from_row(row)

    def get_version(self, template_id: str, version: int) -> TemplateVersion:
        """Get a specific version of a template."""
        stmt = (
            sa.select(template_versions)
            .where(template_versions.c.template_id == template_id)
            .where(template_versions.c.version == version)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        if row is None:
            raise TemplateVersionNotFoundError(
                f"Template version not found: {template_id} v{version}",
                context={"template_id": template_id, "version": version},
            )
        return version_from_row(row)

    def list_versions(self, template_id: str) -> list[TemplateVersion]:
        """List all versions of a template, ordered by version number."""
        stmt = (
            sa.select(template_versions)
            .where(template_versions.c.template_id == template_id)
            .order_by(template_versions.c.version)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [version_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Update (creates new version)
    # ------------------------------------------------------------------

    def update_template(
        self,
        template_id: str,
        *,
        principal_id: str,
        schema: dict[str, Any],
        name: str | None = None,
        description: str | None = None,
        change_reason: str = "",
    ) -> Template:
        """Update a template, creating a new version.

        Only the template owner can update. The template must be active.
        """
        template = self.get_template(template_id)
        self._require_owner(template, principal_id)
        self._require_active(template)

        ts = now_utc()
        new_version = template.current_version + 1
        version_id = generate_id()

        values: dict[str, Any] = {
            "current_version": new_version,
            "schema_json": json.dumps(schema),
        }

        if name is not None:
            values["name"] = name

        if description is not None:
            values["description"] = description

        stmt = (
            sa.update(templates)
            .where(templates.c.id == template_id)
            .values(**values)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        stmt = sa.insert(template_versions).values(
            id=version_id,
            template_id=template_id,
            version=new_version,
            schema_json=json.dumps(schema),
            created_at=ts.isoformat(),
            created_by=principal_id,
            change_reason=change_reason,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        return self.get_template(template_id)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def list_templates(
        self,
        *,
        owner_id: str | None = None,
        template_type: str | None = None,
        scope_id: str | None = None,
        include_archived: bool = False,
    ) -> list[Template]:
        """List templates with optional filters."""
        stmt = sa.select(templates)

        if owner_id is not None:
            stmt = stmt.where(templates.c.owner_id == owner_id)

        if template_type is not None:
            stmt = stmt.where(templates.c.template_type == template_type)

        if scope_id is not None:
            stmt = stmt.where(templates.c.scope_id == scope_id)

        if not include_archived:
            stmt = stmt.where(templates.c.lifecycle != Lifecycle.ARCHIVED.name)

        stmt = stmt.order_by(templates.c.created_at.desc())
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [template_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def archive_template(self, template_id: str, *, principal_id: str) -> Template:
        """Archive a template. Only the owner can archive."""
        template = self.get_template(template_id)
        self._require_owner(template, principal_id)

        stmt = (
            sa.update(templates)
            .where(templates.c.id == template_id)
            .values(lifecycle=Lifecycle.ARCHIVED.name)
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
        return self.get_template(template_id)

    # ------------------------------------------------------------------
    # Instantiation
    # ------------------------------------------------------------------

    def instantiate(
        self,
        template_id: str,
        *,
        overrides: dict[str, Any] | None = None,
        version: int | None = None,
    ) -> InstantiationResult:
        """Instantiate a template, merging defaults with overrides.

        If version is None, uses the current version.
        Returns an InstantiationResult with the merged data.
        The caller is responsible for using the result to create
        the actual construct.
        """
        template = self.get_template(template_id)

        if not template.is_active:
            raise TemplateInstantiationError(
                f"Cannot instantiate archived template: {template_id}",
                context={"template_id": template_id, "lifecycle": template.lifecycle.name},
            )

        if version is not None:
            tv = self.get_version(template_id, version)
            schema = tv.schema
            used_version = tv.version
        else:
            schema = template.schema
            used_version = template.current_version

        overrides = overrides or {}
        merged = _deep_merge(schema, overrides)

        return InstantiationResult(
            template_id=template.id,
            template_name=template.name,
            template_type=template.template_type,
            template_version=used_version,
            data=merged,
            overrides_applied=overrides,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_owner(template: Template, principal_id: str) -> None:
        if template.owner_id != principal_id:
            raise AccessDeniedError(
                "Only the template owner can perform this action",
                context={"template_id": template.id, "owner_id": template.owner_id, "principal_id": principal_id},
            )

    @staticmethod
    def _require_active(template: Template) -> None:
        if template.lifecycle != Lifecycle.ACTIVE:
            raise TemplateInstantiationError(
                f"Template is not active: {template.lifecycle.name}",
                context={"template_id": template.id, "lifecycle": template.lifecycle.name},
            )


# ---------------------------------------------------------------------------
# Deep merge utility
# ---------------------------------------------------------------------------

def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Deep merge overrides into a copy of base.

    - Dict values are merged recursively
    - All other values from overrides replace base values
    - Keys in base not present in overrides are preserved
    """
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
