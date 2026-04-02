"""Automatic secret rotation — wires policy, vault, and scheduling layers.

Provides a ``JobExecutor`` for the scheduling layer that rotates secrets
whose policies have ``auto_rotate=True`` and whose age exceeds
``max_age_seconds``.
"""

from __future__ import annotations

import json
import secrets as _secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa

from scoped.scheduling.queue import JobExecutor
from scoped.secrets.policy import SecretPolicyManager
from scoped.secrets.vault import SecretVault
from scoped.storage._query import compile_for
from scoped.storage._schema import scheduled_actions, secret_policies, secrets
from scoped.storage.interface import StorageBackend
from scoped.types import now_utc

ROTATION_ACTION_TYPE = "secret_auto_rotate"


def _default_value_generator(secret_id: str) -> str:
    """Generate a random 32-byte URL-safe token as the new secret value."""
    return _secrets.token_urlsafe(32)


def make_rotation_executor(
    backend: StorageBackend,
    vault: SecretVault,
    policy_manager: SecretPolicyManager,
    *,
    value_generator: Callable[[str], str] | None = None,
) -> JobExecutor:
    """Return a ``JobExecutor`` that handles ``secret_auto_rotate`` actions.

    The default *value_generator* produces a random 32-byte URL-safe
    token. Supply a custom callable ``(secret_id) -> str`` to generate
    domain-specific values (e.g. database passwords, API keys).
    """
    gen = value_generator or _default_value_generator

    def _execute(action_type: str, action_config: dict[str, Any]) -> dict[str, Any]:
        if action_type != ROTATION_ACTION_TYPE:
            return {"skipped": True, "reason": f"unknown action_type: {action_type}"}

        secret_id = action_config.get("secret_id")
        if not secret_id:
            return {"rotated": False, "reason": "missing secret_id in config"}

        if not policy_manager.needs_rotation(secret_id):
            return {"rotated": False, "secret_id": secret_id, "reason": "not_due"}

        new_value = gen(secret_id)
        version = vault.rotate(
            secret_id,
            new_value=new_value,
            rotated_by="system:auto-rotation",
            reason="auto-rotation policy",
        )

        return {
            "rotated": True,
            "secret_id": secret_id,
            "new_version": version.version,
        }

    return _execute


def schedule_auto_rotations(
    backend: StorageBackend,
    policy_manager: SecretPolicyManager,
    scheduler: Any,
    owner_id: str,
) -> list[Any]:
    """Ensure every ``auto_rotate`` policy has a corresponding scheduled action.

    Scans all policies where ``auto_rotate=1``. For each, computes the
    next rotation time based on the secret's age and ``max_age_seconds``,
    then creates a ``ScheduledAction`` if one does not already exist for
    that secret.

    Returns the list of newly created scheduled actions.
    """
    from scoped.secrets.models import secret_from_row

    stmt = sa.select(secret_policies).where(secret_policies.c.auto_rotate == 1)
    sql, params = compile_for(stmt, backend.dialect)
    rows = backend.fetch_all(sql, params)
    if not rows:
        return []

    # Collect existing auto-rotation actions so we don't duplicate
    stmt = sa.select(scheduled_actions.c.action_config_json).where(
        (scheduled_actions.c.action_type == ROTATION_ACTION_TYPE)
        & (scheduled_actions.c.lifecycle == "ACTIVE"),
    )
    sql, params = compile_for(stmt, backend.dialect)
    existing_actions = backend.fetch_all(sql, params)
    already_scheduled: set[str] = set()
    for action_row in existing_actions:
        cfg = json.loads(action_row["action_config_json"])
        sid = cfg.get("secret_id")
        if sid:
            already_scheduled.add(sid)

    created = []
    for policy_row in rows:
        secret_id = policy_row.get("secret_id")
        if not secret_id or secret_id in already_scheduled:
            continue

        max_age = policy_row.get("max_age_seconds")
        if max_age is None:
            continue

        # Compute next rotation time
        stmt = sa.select(secrets).where(secrets.c.id == secret_id)
        sql, params = compile_for(stmt, backend.dialect)
        secret_row = backend.fetch_one(sql, params)
        if secret_row is None:
            continue

        secret = secret_from_row(secret_row)
        last_rotated = secret.last_rotated_at or secret.created_at
        next_run = last_rotated + timedelta(seconds=max_age)

        # If already overdue, schedule for now
        if next_run < now_utc():
            next_run = now_utc()

        action = scheduler.create_action(
            name=f"auto-rotate-{secret_id[:8]}",
            owner_id=owner_id,
            action_type=ROTATION_ACTION_TYPE,
            action_config={"secret_id": secret_id, "policy_id": policy_row["id"]},
            next_run_at=next_run,
        )
        already_scheduled.add(secret_id)
        created.append(action)

    return created


def run_pending_rotations(
    scheduler: Any,
    job_queue: Any,
) -> list[Any]:
    """Process all due rotation actions.

    Fetches due actions from the scheduler, enqueues them as jobs,
    executes them, and advances the scheduled action's ``next_run_at``.

    Returns the list of completed ``Job`` objects.
    """
    due = scheduler.get_due_actions()
    rotation_actions = [a for a in due if a.action_type == ROTATION_ACTION_TYPE]

    completed = []
    for action in rotation_actions:
        job = job_queue.enqueue(
            name=f"rotate-{action.action_config.get('secret_id', 'unknown')[:8]}",
            action_type=action.action_type,
            action_config=action.action_config,
            owner_id=action.owner_id,
            scheduled_action_id=action.id,
        )
        result = job_queue.run_next()
        if result is not None:
            completed.append(result)

        # Advance to next rotation interval
        max_age = action.action_config.get("max_age_seconds")
        if max_age:
            next_run = now_utc() + timedelta(seconds=max_age)
        else:
            # Fall back: re-query the policy for max_age
            next_run = now_utc() + timedelta(hours=24)

        scheduler.advance_action(action.id, next_run)

    return completed
