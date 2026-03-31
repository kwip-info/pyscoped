"""Test data factories for pyscoped.

``ScopedFactory`` provides a concise API for creating test entities
without manual service wiring::

    factory = ScopedFactory(services)
    alice = factory.principal("Alice")
    doc, v1 = factory.object(alice, data={"title": "Hello"})
    team = factory.scope(alice, name="team", members=[bob])
"""

from __future__ import annotations

from typing import Any

from scoped.tenancy.models import ScopeRole
from scoped.types import generate_id


class ScopedFactory:
    """Convenience factory for creating test entities.

    Args:
        services: A fully-wired ``ScopedServices`` instance.
    """

    def __init__(self, services: Any) -> None:
        self._svc = services
        self._principal_counter = 0

    def principal(
        self,
        name: str | None = None,
        kind: str = "user",
    ) -> Any:
        """Create a principal.

        If *name* is omitted, generates ``"user-1"``, ``"user-2"``, etc.
        """
        self._principal_counter += 1
        display = name or f"user-{self._principal_counter}"
        pid = display.lower().replace(" ", "-")
        return self._svc.principals.create_principal(
            kind=kind,
            display_name=display,
            principal_id=pid,
        )

    def object(
        self,
        owner: Any,
        object_type: str = "Document",
        data: dict[str, Any] | None = None,
    ) -> tuple[Any, Any]:
        """Create a scoped object. Returns ``(ScopedObject, ObjectVersion)``."""
        return self._svc.manager.create(
            object_type=object_type,
            owner_id=owner.id,
            data=data or {"title": f"Test {object_type}"},
        )

    def scope(
        self,
        owner: Any,
        name: str | None = None,
        members: list[Any] | None = None,
    ) -> Any:
        """Create a scope, optionally adding members as editors."""
        scope = self._svc.scopes.create_scope(
            name=name or f"scope-{generate_id()[:8]}",
            owner_id=owner.id,
        )
        for member in (members or []):
            self._svc.scopes.add_member(
                scope_id=scope.id,
                principal_id=member.id,
                role=ScopeRole.EDITOR,
                granted_by=owner.id,
            )
        return scope

    def project(self, obj: Any, scope: Any, *, projected_by: Any) -> None:
        """Project an object into a scope."""
        self._svc.projections.project(
            object_id=obj.id,
            scope_id=scope.id,
            projected_by=projected_by.id,
        )

    def secret(
        self,
        owner: Any,
        name: str = "test-secret",
        value: str = "secret-value",
        classification: str = "standard",
    ) -> tuple[Any, Any]:
        """Create a secret. Returns ``(Secret, SecretVersion)``."""
        return self._svc.secrets.create_secret(
            name=name,
            plaintext_value=value,
            owner_id=owner.id,
            classification=classification,
        )
