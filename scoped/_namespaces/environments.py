"""Environments namespace — ephemeral workspaces.

Usage::

    import scoped

    with scoped.as_principal(alice):
        env = scoped.environments.spawn("Review", metadata={"pr": 42})
        scoped.environments.activate(env)

        scoped.environments.add_object(env, obj)
        snap = scoped.environments.capture(env, name="v1")

        scoped.environments.complete(env)
        scoped.environments.discard(env)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from scoped._namespaces._base import _resolve_principal_id, _to_id

if TYPE_CHECKING:
    from scoped.environments.models import (
        Environment,
        EnvironmentObject,
        EnvironmentSnapshot,
        EnvironmentState,
        EnvironmentTemplate,
        ObjectOrigin,
    )


class EnvironmentsNamespace:
    """Simplified API for Layer 8 environments.

    Wraps ``EnvironmentLifecycle``, ``EnvironmentContainer``, and
    ``SnapshotManager`` with context-aware defaults.
    """

    def __init__(self, services: Any) -> None:
        self._svc = services

    # -- Lifecycle ---------------------------------------------------------

    def spawn(
        self,
        name: str,
        *,
        owner_id: str | None = None,
        description: str = "",
        template_id: str | None = None,
        ephemeral: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> Environment:
        """Spawn a new environment in SPAWNING state."""
        owner = _resolve_principal_id(owner_id)
        return self._svc.environments.spawn(
            name=name, owner_id=owner, description=description,
            template_id=template_id, ephemeral=ephemeral, metadata=metadata,
        )

    def activate(self, env: str | Any, *, actor_id: str | None = None) -> Environment:
        """SPAWNING -> ACTIVE."""
        return self._svc.environments.activate(
            _to_id(env), actor_id=_resolve_principal_id(actor_id),
        )

    def suspend(self, env: str | Any, *, actor_id: str | None = None) -> Environment:
        """ACTIVE -> SUSPENDED."""
        return self._svc.environments.suspend(
            _to_id(env), actor_id=_resolve_principal_id(actor_id),
        )

    def resume(self, env: str | Any, *, actor_id: str | None = None) -> Environment:
        """SUSPENDED -> ACTIVE."""
        return self._svc.environments.resume(
            _to_id(env), actor_id=_resolve_principal_id(actor_id),
        )

    def complete(self, env: str | Any, *, actor_id: str | None = None) -> Environment:
        """ACTIVE -> COMPLETED."""
        return self._svc.environments.complete(
            _to_id(env), actor_id=_resolve_principal_id(actor_id),
        )

    def discard(self, env: str | Any, *, actor_id: str | None = None) -> Environment:
        """COMPLETED/PROMOTED -> DISCARDED."""
        return self._svc.environments.discard(
            _to_id(env), actor_id=_resolve_principal_id(actor_id),
        )

    def promote(self, env: str | Any, *, actor_id: str | None = None) -> Environment:
        """COMPLETED -> PROMOTED."""
        return self._svc.environments.promote(
            _to_id(env), actor_id=_resolve_principal_id(actor_id),
        )

    def get(self, env_id: str) -> Environment | None:
        """Fetch an environment by ID."""
        return self._svc.environments.get(env_id)

    def list(
        self,
        *,
        owner_id: str | None = None,
        state: EnvironmentState | None = None,
        ephemeral: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Environment]:
        """List environments with optional filters."""
        return self._svc.environments.list_environments(
            owner_id=owner_id, state=state, ephemeral=ephemeral,
            limit=limit, offset=offset,
        )

    # -- Templates ---------------------------------------------------------

    def create_template(
        self,
        name: str,
        *,
        owner_id: str | None = None,
        description: str = "",
        config: dict[str, Any] | None = None,
    ) -> EnvironmentTemplate:
        """Create a reusable environment template."""
        owner = _resolve_principal_id(owner_id)
        return self._svc.environments.create_template(
            name=name, owner_id=owner, description=description, config=config,
        )

    def spawn_from_template(
        self,
        template_id: str,
        *,
        owner_id: str | None = None,
        name: str | None = None,
    ) -> Environment:
        """Spawn an environment from a template."""
        owner = _resolve_principal_id(owner_id)
        return self._svc.environments.spawn_from_template(
            template_id, owner_id=owner, name=name,
        )

    # -- Container ---------------------------------------------------------

    def add_object(
        self,
        env: str | Any,
        obj: str | Any,
        *,
        actor_id: str | None = None,
        origin: ObjectOrigin | None = None,
    ) -> EnvironmentObject:
        """Track an object in the environment."""
        from scoped.environments.models import ObjectOrigin as OO
        kwargs: dict[str, Any] = {
            "actor_id": _resolve_principal_id(actor_id),
        }
        if origin is not None:
            kwargs["origin"] = origin
        else:
            kwargs["origin"] = OO.CREATED
        return self._svc.env_container.add_object(
            _to_id(env), _to_id(obj), **kwargs,
        )

    def project_in(
        self,
        env: str | Any,
        obj: str | Any,
        *,
        actor_id: str | None = None,
    ) -> EnvironmentObject:
        """Project an external object into the environment."""
        return self._svc.env_container.project_in(
            _to_id(env), _to_id(obj),
            actor_id=_resolve_principal_id(actor_id),
        )

    def remove_object(
        self,
        env: str | Any,
        obj: str | Any,
        *,
        actor_id: str | None = None,
    ) -> bool:
        """Remove an object from the environment."""
        return self._svc.env_container.remove_object(
            _to_id(env), _to_id(obj),
            actor_id=_resolve_principal_id(actor_id),
        )

    def list_objects(
        self,
        env: str | Any,
        *,
        origin: ObjectOrigin | None = None,
        limit: int = 1000,
    ) -> list[EnvironmentObject]:
        """List objects in an environment."""
        return self._svc.env_container.list_objects(
            _to_id(env), origin=origin, limit=limit,
        )

    def contains(self, env: str | Any, obj: str | Any) -> bool:
        """Check whether an object is in the environment."""
        return self._svc.env_container.contains(_to_id(env), _to_id(obj))

    # -- Snapshots ---------------------------------------------------------

    def capture(
        self,
        env: str | Any,
        *,
        created_by: str | None = None,
        name: str = "",
    ) -> EnvironmentSnapshot:
        """Capture a full snapshot of the environment."""
        return self._svc.env_snapshots.capture(
            _to_id(env),
            created_by=_resolve_principal_id(created_by),
            name=name,
        )

    def restore(
        self,
        snapshot_id: str,
        *,
        restored_by: str | None = None,
    ) -> EnvironmentSnapshot:
        """Restore an environment to a previous snapshot."""
        return self._svc.env_snapshots.restore(
            snapshot_id,
            restored_by=_resolve_principal_id(restored_by),
        )

    def list_snapshots(
        self,
        env: str | Any,
        *,
        limit: int = 100,
    ) -> list[EnvironmentSnapshot]:
        """List snapshots for an environment, newest first."""
        return self._svc.env_snapshots.list_snapshots(_to_id(env), limit=limit)
