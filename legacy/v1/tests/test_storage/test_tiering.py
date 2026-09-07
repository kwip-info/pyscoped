"""Tests for Storage Tiering (A8 — tiering module)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from scoped.storage.tiering import (
    RetentionPolicy,
    StorageTier,
    TierAssignment,
    TierManager,
    TierTransitionCandidate,
    assignment_from_row,
    policy_from_row,
)
from scoped.types import Lifecycle, generate_id, now_utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_principal(backend) -> str:
    pid = generate_id()
    ts = now_utc().isoformat()
    backend.execute(
        "INSERT OR IGNORE INTO registry_entries "
        "(id, urn, kind, namespace, name, registered_at, registered_by) "
        "VALUES ('reg_stub', 'scoped:MODEL:test:stub:1', 'MODEL', 'test', 'stub', ?, 'system')",
        (ts,),
    )
    backend.execute(
        "INSERT INTO principals (id, kind, display_name, registry_entry_id, created_at) "
        "VALUES (?, 'user', 'Test User', 'reg_stub', ?)",
        (pid, ts),
    )
    return pid


def _create_object(backend, owner_id: str, object_type: str = "document") -> tuple[str, int]:
    """Create a scoped object with one version. Returns (object_id, version)."""
    oid = generate_id()
    vid = generate_id()
    ts = now_utc().isoformat()
    backend.execute(
        "INSERT INTO scoped_objects (id, object_type, owner_id, current_version, created_at, lifecycle) "
        "VALUES (?, ?, ?, 1, ?, 'ACTIVE')",
        (oid, object_type, owner_id, ts),
    )
    backend.execute(
        "INSERT INTO object_versions (id, object_id, version, data_json, created_at, created_by) "
        "VALUES (?, ?, 1, '{}', ?, ?)",
        (vid, oid, ts, owner_id),
    )
    return oid, 1


# ===========================================================================
# StorageTier enum
# ===========================================================================

class TestStorageTier:
    def test_values(self):
        assert StorageTier.HOT.value == "HOT"
        assert StorageTier.GLACIAL.value == "GLACIAL"

    def test_rank_ordering(self):
        assert StorageTier.HOT.rank < StorageTier.WARM.rank
        assert StorageTier.WARM.rank < StorageTier.COLD.rank
        assert StorageTier.COLD.rank < StorageTier.GLACIAL.rank


# ===========================================================================
# Row mappers
# ===========================================================================

class TestRowMappers:
    def test_assignment_from_row(self):
        ts = now_utc()
        row = {
            "id": "a1", "object_id": "obj1", "version": 1,
            "tier": "WARM", "assigned_at": ts.isoformat(),
            "assigned_by": "user1", "previous_tier": "HOT",
        }
        a = assignment_from_row(row)
        assert a.tier == StorageTier.WARM
        assert a.previous_tier == StorageTier.HOT

    def test_assignment_no_previous(self):
        ts = now_utc()
        row = {
            "id": "a1", "object_id": "obj1", "version": 1,
            "tier": "HOT", "assigned_at": ts.isoformat(),
            "assigned_by": "user1", "previous_tier": None,
        }
        a = assignment_from_row(row)
        assert a.previous_tier is None

    def test_policy_from_row(self):
        ts = now_utc()
        row = {
            "id": "p1", "name": "Archive old", "description": "desc",
            "source_tier": "HOT", "target_tier": "WARM",
            "condition_type": "age_days", "condition_value": "90",
            "object_type": None, "scope_id": None,
            "owner_id": "user1", "created_at": ts.isoformat(),
            "lifecycle": "ACTIVE",
        }
        p = policy_from_row(row)
        assert p.source_tier == StorageTier.HOT
        assert p.target_tier == StorageTier.WARM
        assert p.is_active is True


# ===========================================================================
# TierManager — assignments
# ===========================================================================

class TestTierAssignments:
    def test_assign_tier(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid, ver = _create_object(sqlite_backend, owner)
        mgr = TierManager(sqlite_backend)

        assignment = mgr.assign_tier(
            object_id=oid, version=ver, tier=StorageTier.HOT, principal_id=owner,
        )

        assert assignment.object_id == oid
        assert assignment.tier == StorageTier.HOT
        assert assignment.previous_tier is None

    def test_change_tier(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid, ver = _create_object(sqlite_backend, owner)
        mgr = TierManager(sqlite_backend)

        mgr.assign_tier(object_id=oid, version=ver, tier=StorageTier.HOT, principal_id=owner)
        updated = mgr.assign_tier(
            object_id=oid, version=ver, tier=StorageTier.WARM, principal_id=owner,
        )

        assert updated.tier == StorageTier.WARM
        assert updated.previous_tier == StorageTier.HOT

    def test_get_tier(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid, ver = _create_object(sqlite_backend, owner)
        mgr = TierManager(sqlite_backend)

        assert mgr.get_tier(oid, ver) is None  # no assignment yet

        mgr.assign_tier(object_id=oid, version=ver, tier=StorageTier.COLD, principal_id=owner)
        result = mgr.get_tier(oid, ver)
        assert result is not None
        assert result.tier == StorageTier.COLD

    def test_get_object_tiers(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid, _ = _create_object(sqlite_backend, owner)
        mgr = TierManager(sqlite_backend)

        # Add a second version
        vid2 = generate_id()
        ts = now_utc().isoformat()
        sqlite_backend.execute(
            "INSERT INTO object_versions (id, object_id, version, data_json, created_at, created_by) "
            "VALUES (?, ?, 2, '{\"v\": 2}', ?, ?)",
            (vid2, oid, ts, owner),
        )

        mgr.assign_tier(object_id=oid, version=1, tier=StorageTier.WARM, principal_id=owner)
        mgr.assign_tier(object_id=oid, version=2, tier=StorageTier.HOT, principal_id=owner)

        tiers = mgr.get_object_tiers(oid)
        assert len(tiers) == 2
        assert tiers[0].version == 1
        assert tiers[0].tier == StorageTier.WARM
        assert tiers[1].version == 2
        assert tiers[1].tier == StorageTier.HOT

    def test_get_objects_in_tier(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid1, v1 = _create_object(sqlite_backend, owner)
        oid2, v2 = _create_object(sqlite_backend, owner)
        mgr = TierManager(sqlite_backend)

        mgr.assign_tier(object_id=oid1, version=v1, tier=StorageTier.HOT, principal_id=owner)
        mgr.assign_tier(object_id=oid2, version=v2, tier=StorageTier.COLD, principal_id=owner)

        hot = mgr.get_objects_in_tier(StorageTier.HOT)
        assert len(hot) == 1
        assert hot[0].object_id == oid1

    def test_get_objects_in_tier_by_type(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid1, v1 = _create_object(sqlite_backend, owner, object_type="document")
        oid2, v2 = _create_object(sqlite_backend, owner, object_type="image")
        mgr = TierManager(sqlite_backend)

        mgr.assign_tier(object_id=oid1, version=v1, tier=StorageTier.HOT, principal_id=owner)
        mgr.assign_tier(object_id=oid2, version=v2, tier=StorageTier.HOT, principal_id=owner)

        docs = mgr.get_objects_in_tier(StorageTier.HOT, object_type="document")
        assert len(docs) == 1
        assert docs[0].object_id == oid1

    def test_count_by_tier(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid1, v1 = _create_object(sqlite_backend, owner)
        oid2, v2 = _create_object(sqlite_backend, owner)
        oid3, v3 = _create_object(sqlite_backend, owner)
        mgr = TierManager(sqlite_backend)

        mgr.assign_tier(object_id=oid1, version=v1, tier=StorageTier.HOT, principal_id=owner)
        mgr.assign_tier(object_id=oid2, version=v2, tier=StorageTier.HOT, principal_id=owner)
        mgr.assign_tier(object_id=oid3, version=v3, tier=StorageTier.COLD, principal_id=owner)

        counts = mgr.count_by_tier()
        assert counts[StorageTier.HOT] == 2
        assert counts[StorageTier.COLD] == 1
        assert counts[StorageTier.WARM] == 0
        assert counts[StorageTier.GLACIAL] == 0


# ===========================================================================
# TierManager — retention policies
# ===========================================================================

class TestRetentionPolicies:
    def test_create_policy(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        mgr = TierManager(sqlite_backend)

        policy = mgr.create_policy(
            name="Archive hot after 90 days",
            source_tier=StorageTier.HOT,
            target_tier=StorageTier.WARM,
            condition_type="age_days",
            condition_value="90",
            owner_id=owner,
        )

        assert policy.name == "Archive hot after 90 days"
        assert policy.source_tier == StorageTier.HOT
        assert policy.target_tier == StorageTier.WARM
        assert policy.lifecycle == Lifecycle.ACTIVE

    def test_create_policy_invalid_direction(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        mgr = TierManager(sqlite_backend)

        with pytest.raises(ValueError, match="colder"):
            mgr.create_policy(
                name="Bad policy",
                source_tier=StorageTier.COLD,
                target_tier=StorageTier.HOT,
                condition_type="age_days",
                condition_value="30",
                owner_id=owner,
            )

    def test_create_policy_invalid_condition(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        mgr = TierManager(sqlite_backend)

        with pytest.raises(ValueError, match="Unknown condition"):
            mgr.create_policy(
                name="Bad",
                source_tier=StorageTier.HOT,
                target_tier=StorageTier.WARM,
                condition_type="invalid_type",
                condition_value="x",
                owner_id=owner,
            )

    def test_get_policy(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        mgr = TierManager(sqlite_backend)
        policy = mgr.create_policy(
            name="P1", source_tier=StorageTier.HOT, target_tier=StorageTier.WARM,
            condition_type="age_days", condition_value="30", owner_id=owner,
        )

        fetched = mgr.get_policy(policy.id)
        assert fetched is not None
        assert fetched.name == "P1"

    def test_get_policy_not_found(self, sqlite_backend):
        mgr = TierManager(sqlite_backend)
        assert mgr.get_policy("nonexistent") is None

    def test_list_policies(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        mgr = TierManager(sqlite_backend)
        mgr.create_policy(
            name="P1", source_tier=StorageTier.HOT, target_tier=StorageTier.WARM,
            condition_type="age_days", condition_value="30", owner_id=owner,
        )
        mgr.create_policy(
            name="P2", source_tier=StorageTier.WARM, target_tier=StorageTier.COLD,
            condition_type="age_days", condition_value="90", owner_id=owner,
        )

        policies = mgr.list_policies()
        assert len(policies) == 2

    def test_archive_policy(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        mgr = TierManager(sqlite_backend)
        policy = mgr.create_policy(
            name="P1", source_tier=StorageTier.HOT, target_tier=StorageTier.WARM,
            condition_type="age_days", condition_value="30", owner_id=owner,
        )

        mgr.archive_policy(policy.id)

        policies = mgr.list_policies()
        assert len(policies) == 0

        policies_all = mgr.list_policies(include_archived=True)
        assert len(policies_all) == 1


# ===========================================================================
# TierManager — policy evaluation
# ===========================================================================

class TestPolicyEvaluation:
    def test_evaluate_age_policy_no_match(self, sqlite_backend):
        """Fresh assignments should not match an age_days policy."""
        owner = _setup_principal(sqlite_backend)
        oid, ver = _create_object(sqlite_backend, owner)
        mgr = TierManager(sqlite_backend)

        mgr.assign_tier(object_id=oid, version=ver, tier=StorageTier.HOT, principal_id=owner)
        mgr.create_policy(
            name="Cool after 30 days",
            source_tier=StorageTier.HOT,
            target_tier=StorageTier.WARM,
            condition_type="age_days",
            condition_value="30",
            owner_id=owner,
        )

        candidates = mgr.evaluate_policies()
        assert len(candidates) == 0

    def test_evaluate_age_policy_match(self, sqlite_backend):
        """Old assignments should match an age_days policy."""
        owner = _setup_principal(sqlite_backend)
        oid, ver = _create_object(sqlite_backend, owner)
        mgr = TierManager(sqlite_backend)

        mgr.assign_tier(object_id=oid, version=ver, tier=StorageTier.HOT, principal_id=owner)

        # Backdate the assignment
        old_date = (now_utc() - timedelta(days=60)).isoformat()
        sqlite_backend.execute(
            "UPDATE tier_assignments SET assigned_at = ? WHERE object_id = ? AND version = ?",
            (old_date, oid, ver),
        )

        mgr.create_policy(
            name="Cool after 30 days",
            source_tier=StorageTier.HOT,
            target_tier=StorageTier.WARM,
            condition_type="age_days",
            condition_value="30",
            owner_id=owner,
        )

        candidates = mgr.evaluate_policies()
        assert len(candidates) == 1
        assert candidates[0].object_id == oid
        assert candidates[0].target_tier == StorageTier.WARM

    def test_evaluate_lifecycle_policy(self, sqlite_backend):
        """Lifecycle-based policy should match archived objects."""
        owner = _setup_principal(sqlite_backend)
        oid, ver = _create_object(sqlite_backend, owner)
        mgr = TierManager(sqlite_backend)

        # Archive the object
        sqlite_backend.execute(
            "UPDATE scoped_objects SET lifecycle = 'ARCHIVED' WHERE id = ?",
            (oid,),
        )

        mgr.assign_tier(object_id=oid, version=ver, tier=StorageTier.WARM, principal_id=owner)
        mgr.create_policy(
            name="Archive archived objects",
            source_tier=StorageTier.WARM,
            target_tier=StorageTier.COLD,
            condition_type="lifecycle_state",
            condition_value="ARCHIVED",
            owner_id=owner,
        )

        candidates = mgr.evaluate_policies()
        assert len(candidates) == 1
        assert candidates[0].target_tier == StorageTier.COLD

    def test_evaluate_policy_type_filter(self, sqlite_backend):
        """Policy with object_type filter only matches that type."""
        owner = _setup_principal(sqlite_backend)
        oid1, v1 = _create_object(sqlite_backend, owner, object_type="document")
        oid2, v2 = _create_object(sqlite_backend, owner, object_type="image")
        mgr = TierManager(sqlite_backend)

        mgr.assign_tier(object_id=oid1, version=v1, tier=StorageTier.HOT, principal_id=owner)
        mgr.assign_tier(object_id=oid2, version=v2, tier=StorageTier.HOT, principal_id=owner)

        # Backdate both
        old_date = (now_utc() - timedelta(days=60)).isoformat()
        sqlite_backend.execute(
            "UPDATE tier_assignments SET assigned_at = ?", (old_date,)
        )

        mgr.create_policy(
            name="Cool documents",
            source_tier=StorageTier.HOT,
            target_tier=StorageTier.WARM,
            condition_type="age_days",
            condition_value="30",
            owner_id=owner,
            object_type="document",
        )

        candidates = mgr.evaluate_policies()
        assert len(candidates) == 1
        assert candidates[0].object_id == oid1

    def test_apply_transitions(self, sqlite_backend):
        owner = _setup_principal(sqlite_backend)
        oid, ver = _create_object(sqlite_backend, owner)
        mgr = TierManager(sqlite_backend)

        mgr.assign_tier(object_id=oid, version=ver, tier=StorageTier.HOT, principal_id=owner)

        candidates = [
            TierTransitionCandidate(
                object_id=oid, version=ver,
                current_tier=StorageTier.HOT, target_tier=StorageTier.WARM,
                policy_id="p1", reason="test",
            )
        ]

        count = mgr.apply_transitions(candidates, principal_id=owner)
        assert count == 1

        result = mgr.get_tier(oid, ver)
        assert result is not None
        assert result.tier == StorageTier.WARM
        assert result.previous_tier == StorageTier.HOT
