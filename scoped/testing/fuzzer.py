"""IsolationFuzzer — randomized access pattern testing.

Generates random principals, objects, scopes, and projections, then
verifies that access matches what the framework says should be allowed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.tenancy.lifecycle import ScopeLifecycle
from scoped.tenancy.projection import ProjectionManager
from scoped.storage.interface import StorageBackend
from scoped.tenancy.models import ScopeRole
from scoped.types import generate_id, now_utc


@dataclass(frozen=True, slots=True)
class FuzzResult:
    """Result of a fuzzing run."""

    principals_created: int
    objects_created: int
    scopes_created: int
    projections_created: int
    access_checks: int
    violations: tuple[str, ...]
    """Descriptions of isolation violations found."""

    @property
    def passed(self) -> bool:
        return len(self.violations) == 0


class IsolationFuzzer:
    """Generate random access patterns and verify isolation holds.

    Creates a random graph of principals, objects, scopes, and projections,
    then checks every (principal, object) pair to verify access control
    matches what the framework says.
    """

    def __init__(
        self,
        backend: StorageBackend,
        *,
        seed: int | None = None,
    ) -> None:
        self._backend = backend
        self._rng = random.Random(seed)
        self._manager = ScopedManager(backend)
        self._principals = PrincipalStore(backend)
        self._scopes = ScopeLifecycle(backend)
        self._projections = ProjectionManager(backend)

    def run(
        self,
        *,
        num_principals: int = 5,
        num_objects: int = 10,
        num_scopes: int = 3,
        num_projections: int = 5,
        num_mutations: int = 5,
    ) -> FuzzResult:
        """Execute a full fuzzing run.

        1. Create random principals
        2. Create random objects with random owners
        3. Create random scopes with random memberships
        4. Create random projections
        5. Check all (principal, object) access pairs
        6. Apply random mutations and re-check
        """
        # Step 1: Create principals
        principal_ids = self._create_principals(num_principals)

        # Step 2: Create objects with random owners
        objects = self._create_objects(num_objects, principal_ids)

        # Step 3: Create scopes with random owners and members
        scope_data = self._create_scopes(num_scopes, principal_ids)

        # Step 4: Create random projections
        proj_count = self._create_projections(
            num_projections, objects, scope_data, principal_ids,
        )

        # Step 5: Check all access pairs
        violations = self._check_all_access(principal_ids, objects, scope_data)

        # Step 6: Apply mutations and re-check
        for _ in range(num_mutations):
            mutation_type = self._rng.choice(["add_member", "remove_projection", "add_projection"])
            if mutation_type == "add_member" and scope_data:
                scope = self._rng.choice(scope_data)
                new_member = self._rng.choice(principal_ids)
                if new_member not in scope["members"] and new_member != scope["owner"]:
                    try:
                        self._scopes.add_member(
                            scope["id"], principal_id=new_member,
                            role=ScopeRole.VIEWER, granted_by=scope["owner"],
                        )
                        scope["members"].append(new_member)
                    except Exception:
                        pass
            elif mutation_type == "remove_projection" and scope_data:
                scope = self._rng.choice(scope_data)
                if scope["projected_objects"]:
                    obj_id = self._rng.choice(scope["projected_objects"])
                    try:
                        self._projections.revoke_projection(object_id=obj_id, scope_id=scope["id"])
                        scope["projected_objects"].remove(obj_id)
                    except Exception:
                        pass
            elif mutation_type == "add_projection" and scope_data and objects:
                scope = self._rng.choice(scope_data)
                obj = self._rng.choice(objects)
                if obj["id"] not in scope["projected_objects"] and obj["owner"] == scope["owner"]:
                    try:
                        self._projections.project(
                            object_id=obj["id"],
                            scope_id=scope["id"],
                            projected_by=obj["owner"],
                        )
                        scope["projected_objects"].append(obj["id"])
                    except Exception:
                        pass

            # Re-check after mutation
            violations.extend(
                self._check_all_access(principal_ids, objects, scope_data)
            )

        return FuzzResult(
            principals_created=len(principal_ids),
            objects_created=len(objects),
            scopes_created=len(scope_data),
            projections_created=proj_count,
            access_checks=len(principal_ids) * len(objects) * (1 + num_mutations),
            violations=tuple(violations),
        )

    def _create_principals(self, count: int) -> list[str]:
        """Create random principals and return their IDs."""
        ids = []
        for i in range(count):
            p = self._principals.create_principal(
                kind="user",
                display_name=f"fuzz_user_{i}",
            )
            ids.append(p.id)
        return ids

    def _create_objects(
        self, count: int, principal_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Create random objects with random owners."""
        objects = []
        for i in range(count):
            owner = self._rng.choice(principal_ids)
            obj, _ = self._manager.create(
                object_type="fuzz_object",
                owner_id=owner,
                data={"index": i},
            )
            objects.append({"id": obj.id, "owner": owner})
        return objects

    def _create_scopes(
        self, count: int, principal_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Create random scopes with random members."""
        scopes = []
        for i in range(count):
            owner = self._rng.choice(principal_ids)
            scope = self._scopes.create_scope(
                name=f"fuzz_scope_{i}",
                owner_id=owner,
            )
            # Add random members
            members = []
            for pid in principal_ids:
                if pid != owner and self._rng.random() < 0.4:
                    self._scopes.add_member(
                        scope.id, principal_id=pid,
                        role=ScopeRole.VIEWER, granted_by=owner,
                    )
                    members.append(pid)

            scopes.append({
                "id": scope.id,
                "owner": owner,
                "members": members,
                "projected_objects": [],
            })
        return scopes

    def _create_projections(
        self,
        count: int,
        objects: list[dict[str, Any]],
        scopes: list[dict[str, Any]],
        principal_ids: list[str],
    ) -> int:
        """Create random projections."""
        if not scopes or not objects:
            return 0

        created = 0
        for _ in range(count):
            scope = self._rng.choice(scopes)
            # Pick an object owned by the scope owner
            owner_objects = [o for o in objects if o["owner"] == scope["owner"]]
            if not owner_objects:
                continue
            obj = self._rng.choice(owner_objects)
            if obj["id"] in scope["projected_objects"]:
                continue
            try:
                self._projections.project(
                    object_id=obj["id"],
                    scope_id=scope["id"],
                    projected_by=scope["owner"],
                )
                scope["projected_objects"].append(obj["id"])
                created += 1
            except Exception:
                pass
        return created

    def _check_all_access(
        self,
        principal_ids: list[str],
        objects: list[dict[str, Any]],
        scopes: list[dict[str, Any]],
    ) -> list[str]:
        """Check all (principal, object) pairs for access correctness."""
        violations = []

        for pid in principal_ids:
            for obj in objects:
                result = self._manager.get(obj["id"], principal_id=pid)

                # Determine expected access
                should_access = self._should_have_access(pid, obj, scopes)

                if should_access and result is None:
                    violations.append(
                        f"Principal {pid} should access object {obj['id']} "
                        f"but was denied"
                    )
                elif not should_access and result is not None:
                    violations.append(
                        f"Principal {pid} should NOT access object {obj['id']} "
                        f"but was allowed"
                    )

        return violations

    def _should_have_access(
        self,
        principal_id: str,
        obj: dict[str, Any],
        scopes: list[dict[str, Any]],
    ) -> bool:
        """Determine if a principal should have access to an object.

        The ScopedManager enforces owner-only access at the object level.
        Scope projections enable visibility through separate query paths
        (VisibilityEngine), not through ScopedManager.get().

        So for fuzzing ScopedManager, access is owner-only.
        """
        return obj["owner"] == principal_id
