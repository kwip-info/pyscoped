"""Tests for automatic secret rotation wiring."""

from __future__ import annotations

from datetime import timedelta

import pytest

from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.scheduling.queue import JobQueue
from scoped.scheduling.scheduler import Scheduler
from scoped.secrets.backend import InMemoryBackend
from scoped.secrets.policy import SecretPolicyManager
from scoped.secrets.rotation import (
    ROTATION_ACTION_TYPE,
    make_rotation_executor,
    run_pending_rotations,
    schedule_auto_rotations,
)
from scoped.secrets.vault import SecretVault
from scoped.types import now_utc


@pytest.fixture
def services(sqlite_backend, registry):
    """Build minimal service set for rotation tests."""
    principals = PrincipalStore(sqlite_backend)
    encryption = InMemoryBackend()
    obj_manager = ScopedManager(sqlite_backend)

    owner = principals.create_principal(kind="user", display_name="system", principal_id="system-rot")
    vault = SecretVault(sqlite_backend, encryption, object_manager=obj_manager)
    policy_mgr = SecretPolicyManager(sqlite_backend)
    scheduler = Scheduler(sqlite_backend)

    return {
        "backend": sqlite_backend,
        "vault": vault,
        "encryption": encryption,
        "policy_mgr": policy_mgr,
        "scheduler": scheduler,
        "owner": owner,
    }


class TestMakeRotationExecutor:
    def test_rotates_when_due(self, services):
        vault = services["vault"]
        policy_mgr = services["policy_mgr"]
        owner = services["owner"]
        backend = services["backend"]

        # Create a secret
        secret, _ = vault.create_secret(
            name="api-key",
            plaintext_value="old-value",
            owner_id=owner.id,
        )

        # Create policy with very short max_age so it's immediately due
        policy_mgr.create_policy(
            created_by=owner.id,
            secret_id=secret.id,
            max_age_seconds=0,
            auto_rotate=True,
        )

        executor = make_rotation_executor(backend, vault, policy_mgr)
        result = executor(ROTATION_ACTION_TYPE, {"secret_id": secret.id})

        assert result["rotated"] is True
        assert result["secret_id"] == secret.id
        assert result["new_version"] == 2

    def test_skips_when_not_due(self, services):
        vault = services["vault"]
        policy_mgr = services["policy_mgr"]
        owner = services["owner"]
        backend = services["backend"]

        secret, _ = vault.create_secret(
            name="fresh-key",
            plaintext_value="value",
            owner_id=owner.id,
        )

        # Very long max_age — not due
        policy_mgr.create_policy(
            created_by=owner.id,
            secret_id=secret.id,
            max_age_seconds=999999,
            auto_rotate=True,
        )

        executor = make_rotation_executor(backend, vault, policy_mgr)
        result = executor(ROTATION_ACTION_TYPE, {"secret_id": secret.id})

        assert result["rotated"] is False
        assert result["reason"] == "not_due"

    def test_custom_value_generator(self, services):
        vault = services["vault"]
        policy_mgr = services["policy_mgr"]
        owner = services["owner"]
        backend = services["backend"]

        secret, _ = vault.create_secret(
            name="custom-gen",
            plaintext_value="old",
            owner_id=owner.id,
        )

        policy_mgr.create_policy(
            created_by=owner.id,
            secret_id=secret.id,
            max_age_seconds=0,
            auto_rotate=True,
        )

        custom_values = []

        def my_generator(sid: str) -> str:
            custom_values.append(sid)
            return "custom-generated-value"

        executor = make_rotation_executor(
            backend, vault, policy_mgr, value_generator=my_generator
        )
        result = executor(ROTATION_ACTION_TYPE, {"secret_id": secret.id})

        assert result["rotated"] is True
        assert custom_values == [secret.id]

    def test_unknown_action_type(self, services):
        executor = make_rotation_executor(
            services["backend"],
            services["vault"],
            services["policy_mgr"],
        )
        result = executor("some_other_action", {})
        assert result["skipped"] is True

    def test_missing_secret_id(self, services):
        executor = make_rotation_executor(
            services["backend"],
            services["vault"],
            services["policy_mgr"],
        )
        result = executor(ROTATION_ACTION_TYPE, {})
        assert result["rotated"] is False
        assert result["reason"] == "missing secret_id in config"


class TestScheduleAutoRotations:
    def test_creates_scheduled_actions(self, services):
        vault = services["vault"]
        policy_mgr = services["policy_mgr"]
        scheduler = services["scheduler"]
        owner = services["owner"]
        backend = services["backend"]

        secret, _ = vault.create_secret(
            name="scheduled-key",
            plaintext_value="val",
            owner_id=owner.id,
        )

        policy_mgr.create_policy(
            created_by=owner.id,
            secret_id=secret.id,
            max_age_seconds=3600,
            auto_rotate=True,
        )

        actions = schedule_auto_rotations(backend, policy_mgr, scheduler, owner.id)
        assert len(actions) == 1
        assert actions[0].action_type == ROTATION_ACTION_TYPE
        assert actions[0].action_config["secret_id"] == secret.id

    def test_no_duplicates(self, services):
        vault = services["vault"]
        policy_mgr = services["policy_mgr"]
        scheduler = services["scheduler"]
        owner = services["owner"]
        backend = services["backend"]

        secret, _ = vault.create_secret(
            name="no-dup",
            plaintext_value="val",
            owner_id=owner.id,
        )

        policy_mgr.create_policy(
            created_by=owner.id,
            secret_id=secret.id,
            max_age_seconds=3600,
            auto_rotate=True,
        )

        first = schedule_auto_rotations(backend, policy_mgr, scheduler, owner.id)
        second = schedule_auto_rotations(backend, policy_mgr, scheduler, owner.id)

        assert len(first) == 1
        assert len(second) == 0  # already scheduled

    def test_skips_non_auto_rotate(self, services):
        vault = services["vault"]
        policy_mgr = services["policy_mgr"]
        scheduler = services["scheduler"]
        owner = services["owner"]
        backend = services["backend"]

        secret, _ = vault.create_secret(
            name="manual-only",
            plaintext_value="val",
            owner_id=owner.id,
        )

        policy_mgr.create_policy(
            created_by=owner.id,
            secret_id=secret.id,
            max_age_seconds=3600,
            auto_rotate=False,
        )

        actions = schedule_auto_rotations(backend, policy_mgr, scheduler, owner.id)
        assert len(actions) == 0


class TestRunPendingRotations:
    def test_executes_due_rotations(self, services):
        vault = services["vault"]
        policy_mgr = services["policy_mgr"]
        scheduler = services["scheduler"]
        owner = services["owner"]
        backend = services["backend"]

        secret, _ = vault.create_secret(
            name="pending-rot",
            plaintext_value="old",
            owner_id=owner.id,
        )

        policy_mgr.create_policy(
            created_by=owner.id,
            secret_id=secret.id,
            max_age_seconds=0,
            auto_rotate=True,
        )

        # Schedule the rotation (will be immediately due since max_age=0)
        schedule_auto_rotations(backend, policy_mgr, scheduler, owner.id)

        executor = make_rotation_executor(backend, vault, policy_mgr)
        job_queue = JobQueue(backend, executor=executor)

        jobs = run_pending_rotations(scheduler, job_queue)
        assert len(jobs) == 1

        # Verify the secret was actually rotated
        row = backend.fetch_one(
            "SELECT current_version FROM secrets WHERE id = ?", (secret.id,)
        )
        assert row["current_version"] == 2
