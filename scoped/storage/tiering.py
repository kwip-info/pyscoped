"""Storage tiering — manage object version lifecycle across storage tiers.

Tiers: HOT (active), WARM (recent), COLD (archived), GLACIAL (compressed + sealed).
Objects move between tiers based on retention policies. The TierManager tracks
assignments and evaluates policies to identify candidates for tier transitions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from scoped.storage.interface import StorageBackend
from scoped.types import Lifecycle, generate_id, now_utc


# ---------------------------------------------------------------------------
# StorageTier enum
# ---------------------------------------------------------------------------

class StorageTier(Enum):
    """Storage tiers from hottest (most accessible) to coldest."""
    HOT = "HOT"         # Active data — fast access, full queryability
    WARM = "WARM"       # Recent data — still fast, candidate for cooling
    COLD = "COLD"       # Archived data — slower access, read-only
    GLACIAL = "GLACIAL" # Sealed archives — compressed, integrity-verified, immutable

    @property
    def rank(self) -> int:
        """Numeric rank: HOT=0, WARM=1, COLD=2, GLACIAL=3."""
        return _TIER_RANKS[self]


_TIER_RANKS = {
    StorageTier.HOT: 0,
    StorageTier.WARM: 1,
    StorageTier.COLD: 2,
    StorageTier.GLACIAL: 3,
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TierAssignment:
    """Tracks which tier an object version is currently in."""

    id: str
    object_id: str
    version: int
    tier: StorageTier
    assigned_at: datetime
    assigned_by: str
    previous_tier: StorageTier | None


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """A rule that governs automatic tier transitions."""

    id: str
    name: str
    description: str
    source_tier: StorageTier
    target_tier: StorageTier
    condition_type: str          # "age_days", "lifecycle_state"
    condition_value: str         # e.g. "90" for 90 days, "ARCHIVED" for lifecycle
    object_type: str | None      # optional filter
    scope_id: str | None         # optional filter
    owner_id: str
    created_at: datetime
    lifecycle: Lifecycle

    @property
    def is_active(self) -> bool:
        return self.lifecycle == Lifecycle.ACTIVE


@dataclass(frozen=True, slots=True)
class TierTransitionCandidate:
    """An object version that a retention policy has identified for transition."""

    object_id: str
    version: int
    current_tier: StorageTier
    target_tier: StorageTier
    policy_id: str
    reason: str


# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------

def assignment_from_row(row: dict[str, Any]) -> TierAssignment:
    return TierAssignment(
        id=row["id"],
        object_id=row["object_id"],
        version=row["version"],
        tier=StorageTier(row["tier"]),
        assigned_at=datetime.fromisoformat(row["assigned_at"]),
        assigned_by=row["assigned_by"],
        previous_tier=StorageTier(row["previous_tier"]) if row.get("previous_tier") else None,
    )


def policy_from_row(row: dict[str, Any]) -> RetentionPolicy:
    return RetentionPolicy(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        source_tier=StorageTier(row["source_tier"]),
        target_tier=StorageTier(row["target_tier"]),
        condition_type=row["condition_type"],
        condition_value=row["condition_value"],
        object_type=row.get("object_type"),
        scope_id=row.get("scope_id"),
        owner_id=row["owner_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        lifecycle=Lifecycle[row["lifecycle"]],
    )


# ---------------------------------------------------------------------------
# TierManager
# ---------------------------------------------------------------------------

class TierManager:
    """Manages storage tier assignments and retention policies."""

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    # ------------------------------------------------------------------
    # Tier assignments
    # ------------------------------------------------------------------

    def assign_tier(
        self,
        *,
        object_id: str,
        version: int,
        tier: StorageTier,
        principal_id: str,
    ) -> TierAssignment:
        """Assign or change the tier for an object version."""
        existing = self.get_tier(object_id, version)
        previous_tier = existing.tier if existing else None

        ts = now_utc()
        assignment_id = generate_id()

        if existing:
            # Update existing assignment
            self._backend.execute(
                "UPDATE tier_assignments SET tier = ?, assigned_at = ?, "
                "assigned_by = ?, previous_tier = ? "
                "WHERE object_id = ? AND version = ?",
                (tier.value, ts.isoformat(), principal_id,
                 previous_tier.value if previous_tier else None,
                 object_id, version),
            )
            return TierAssignment(
                id=existing.id,
                object_id=object_id,
                version=version,
                tier=tier,
                assigned_at=ts,
                assigned_by=principal_id,
                previous_tier=previous_tier,
            )
        else:
            # Create new assignment
            self._backend.execute(
                "INSERT INTO tier_assignments "
                "(id, object_id, version, tier, assigned_at, assigned_by, previous_tier) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (assignment_id, object_id, version, tier.value,
                 ts.isoformat(), principal_id, None),
            )
            return TierAssignment(
                id=assignment_id,
                object_id=object_id,
                version=version,
                tier=tier,
                assigned_at=ts,
                assigned_by=principal_id,
                previous_tier=None,
            )

    def get_tier(self, object_id: str, version: int) -> TierAssignment | None:
        """Get the current tier assignment for an object version."""
        row = self._backend.fetch_one(
            "SELECT * FROM tier_assignments WHERE object_id = ? AND version = ?",
            (object_id, version),
        )
        return assignment_from_row(row) if row else None

    def get_object_tiers(self, object_id: str) -> list[TierAssignment]:
        """Get tier assignments for all versions of an object."""
        rows = self._backend.fetch_all(
            "SELECT * FROM tier_assignments WHERE object_id = ? ORDER BY version",
            (object_id,),
        )
        return [assignment_from_row(r) for r in rows]

    def get_objects_in_tier(
        self,
        tier: StorageTier,
        *,
        object_type: str | None = None,
        limit: int = 100,
    ) -> list[TierAssignment]:
        """List all object versions currently in a specific tier."""
        if object_type is not None:
            rows = self._backend.fetch_all(
                "SELECT ta.* FROM tier_assignments ta "
                "JOIN scoped_objects so ON ta.object_id = so.id "
                "WHERE ta.tier = ? AND so.object_type = ? "
                "ORDER BY ta.assigned_at DESC LIMIT ?",
                (tier.value, object_type, limit),
            )
        else:
            rows = self._backend.fetch_all(
                "SELECT * FROM tier_assignments WHERE tier = ? "
                "ORDER BY assigned_at DESC LIMIT ?",
                (tier.value, limit),
            )
        return [assignment_from_row(r) for r in rows]

    def count_by_tier(self) -> dict[StorageTier, int]:
        """Count object versions in each tier."""
        rows = self._backend.fetch_all(
            "SELECT tier, COUNT(*) as cnt FROM tier_assignments GROUP BY tier",
            (),
        )
        result = {t: 0 for t in StorageTier}
        for row in rows:
            result[StorageTier(row["tier"])] = row["cnt"]
        return result

    # ------------------------------------------------------------------
    # Retention policies
    # ------------------------------------------------------------------

    def create_policy(
        self,
        *,
        name: str,
        source_tier: StorageTier,
        target_tier: StorageTier,
        condition_type: str,
        condition_value: str,
        owner_id: str,
        description: str = "",
        object_type: str | None = None,
        scope_id: str | None = None,
    ) -> RetentionPolicy:
        """Create a retention policy for automatic tier transitions."""
        if target_tier.rank <= source_tier.rank:
            raise ValueError(
                f"Target tier ({target_tier.name}) must be colder than "
                f"source tier ({source_tier.name})"
            )
        if condition_type not in ("age_days", "lifecycle_state"):
            raise ValueError(f"Unknown condition type: {condition_type}")

        ts = now_utc()
        policy_id = generate_id()

        self._backend.execute(
            "INSERT INTO retention_policies "
            "(id, name, description, source_tier, target_tier, "
            "condition_type, condition_value, object_type, scope_id, "
            "owner_id, created_at, lifecycle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                policy_id, name, description,
                source_tier.value, target_tier.value,
                condition_type, condition_value,
                object_type, scope_id,
                owner_id, ts.isoformat(), Lifecycle.ACTIVE.name,
            ),
        )

        return RetentionPolicy(
            id=policy_id,
            name=name,
            description=description,
            source_tier=source_tier,
            target_tier=target_tier,
            condition_type=condition_type,
            condition_value=condition_value,
            object_type=object_type,
            scope_id=scope_id,
            owner_id=owner_id,
            created_at=ts,
            lifecycle=Lifecycle.ACTIVE,
        )

    def get_policy(self, policy_id: str) -> RetentionPolicy | None:
        """Get a retention policy by ID."""
        row = self._backend.fetch_one(
            "SELECT * FROM retention_policies WHERE id = ?", (policy_id,)
        )
        return policy_from_row(row) if row else None

    def list_policies(self, *, include_archived: bool = False) -> list[RetentionPolicy]:
        """List all retention policies."""
        if include_archived:
            rows = self._backend.fetch_all(
                "SELECT * FROM retention_policies ORDER BY created_at DESC", ()
            )
        else:
            rows = self._backend.fetch_all(
                "SELECT * FROM retention_policies WHERE lifecycle != ? ORDER BY created_at DESC",
                (Lifecycle.ARCHIVED.name,),
            )
        return [policy_from_row(r) for r in rows]

    def archive_policy(self, policy_id: str) -> None:
        """Archive (disable) a retention policy."""
        self._backend.execute(
            "UPDATE retention_policies SET lifecycle = ? WHERE id = ?",
            (Lifecycle.ARCHIVED.name, policy_id),
        )

    # ------------------------------------------------------------------
    # Policy evaluation
    # ------------------------------------------------------------------

    def evaluate_policies(self) -> list[TierTransitionCandidate]:
        """Evaluate all active policies and return candidates for transition.

        Does NOT apply the transitions — the caller decides what to execute.
        """
        policies = self.list_policies()
        candidates: list[TierTransitionCandidate] = []

        for policy in policies:
            new_candidates = self._evaluate_policy(policy)
            candidates.extend(new_candidates)

        return candidates

    def apply_transitions(
        self,
        candidates: list[TierTransitionCandidate],
        *,
        principal_id: str,
    ) -> int:
        """Apply a list of tier transitions. Returns count of transitions applied."""
        count = 0
        for candidate in candidates:
            self.assign_tier(
                object_id=candidate.object_id,
                version=candidate.version,
                tier=candidate.target_tier,
                principal_id=principal_id,
            )
            count += 1
        return count

    def _evaluate_policy(self, policy: RetentionPolicy) -> list[TierTransitionCandidate]:
        """Evaluate a single policy against current assignments."""
        candidates: list[TierTransitionCandidate] = []

        # Get all assignments in the source tier
        if policy.object_type is not None:
            rows = self._backend.fetch_all(
                "SELECT ta.* FROM tier_assignments ta "
                "JOIN scoped_objects so ON ta.object_id = so.id "
                "WHERE ta.tier = ? AND so.object_type = ?",
                (policy.source_tier.value, policy.object_type),
            )
        else:
            rows = self._backend.fetch_all(
                "SELECT * FROM tier_assignments WHERE tier = ?",
                (policy.source_tier.value,),
            )

        assignments = [assignment_from_row(r) for r in rows]

        for assignment in assignments:
            if self._matches_condition(assignment, policy):
                candidates.append(TierTransitionCandidate(
                    object_id=assignment.object_id,
                    version=assignment.version,
                    current_tier=assignment.tier,
                    target_tier=policy.target_tier,
                    policy_id=policy.id,
                    reason=f"Policy '{policy.name}': {policy.condition_type}={policy.condition_value}",
                ))

        return candidates

    def _matches_condition(
        self,
        assignment: TierAssignment,
        policy: RetentionPolicy,
    ) -> bool:
        """Check if an assignment matches a policy's condition."""
        if policy.condition_type == "age_days":
            age_days = int(policy.condition_value)
            cutoff = now_utc() - timedelta(days=age_days)
            return assignment.assigned_at < cutoff

        if policy.condition_type == "lifecycle_state":
            # Check if the object's lifecycle matches the condition
            row = self._backend.fetch_one(
                "SELECT lifecycle FROM scoped_objects WHERE id = ?",
                (assignment.object_id,),
            )
            if row is None:
                return False
            return row["lifecycle"] == policy.condition_value

        return False
