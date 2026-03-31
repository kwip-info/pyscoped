"""Secret policy management — rotation, access restrictions."""

from __future__ import annotations

import json
from typing import Any

from scoped.secrets.models import (
    SecretPolicy,
    policy_from_row,
)
from scoped.storage.interface import StorageBackend
from scoped.types import generate_id, now_utc


class SecretPolicyManager:
    """Create and evaluate secret policies."""

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def create_policy(
        self,
        *,
        created_by: str,
        secret_id: str | None = None,
        classification: str | None = None,
        max_age_seconds: int | None = None,
        auto_rotate: bool = False,
        allowed_scopes: list[str] | None = None,
        allowed_envs: list[str] | None = None,
    ) -> SecretPolicy:
        ts = now_utc()
        pid = generate_id()
        policy = SecretPolicy(
            id=pid,
            secret_id=secret_id,
            classification=classification,
            max_age_seconds=max_age_seconds,
            auto_rotate=auto_rotate,
            allowed_scopes=allowed_scopes or [],
            allowed_envs=allowed_envs or [],
            created_at=ts,
            created_by=created_by,
        )
        self._backend.execute(
            """INSERT INTO secret_policies
               (id, secret_id, classification, max_age_seconds, auto_rotate,
                allowed_scopes, allowed_envs, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pid, secret_id, classification, max_age_seconds,
             int(auto_rotate), json.dumps(policy.allowed_scopes),
             json.dumps(policy.allowed_envs), ts.isoformat(), created_by),
        )
        return policy

    def get_policy(self, policy_id: str) -> SecretPolicy | None:
        row = self._backend.fetch_one(
            "SELECT * FROM secret_policies WHERE id = ?", (policy_id,),
        )
        return policy_from_row(row) if row else None

    def get_policies_for_secret(self, secret_id: str) -> list[SecretPolicy]:
        """Get policies that apply to a specific secret (by id or classification)."""
        # Get secret's classification
        secret_row = self._backend.fetch_one(
            "SELECT classification FROM secrets WHERE id = ?", (secret_id,),
        )
        if secret_row is None:
            return []

        rows = self._backend.fetch_all(
            """SELECT * FROM secret_policies
               WHERE secret_id = ? OR classification = ?
               ORDER BY created_at""",
            (secret_id, secret_row["classification"]),
        )
        return [policy_from_row(r) for r in rows]

    def check_scope_allowed(self, secret_id: str, scope_id: str) -> bool:
        """Check if a scope is allowed by policies for this secret."""
        policies = self.get_policies_for_secret(secret_id)
        if not policies:
            return True  # no policy = no restriction
        for p in policies:
            if p.allowed_scopes and scope_id not in p.allowed_scopes:
                return False
        return True

    def check_env_allowed(self, secret_id: str, environment_id: str) -> bool:
        """Check if an environment is allowed by policies for this secret."""
        policies = self.get_policies_for_secret(secret_id)
        if not policies:
            return True
        for p in policies:
            if p.allowed_envs and environment_id not in p.allowed_envs:
                return False
        return True

    def needs_rotation(self, secret_id: str) -> bool:
        """Check if a secret needs rotation based on policy max_age."""
        from scoped.secrets.models import secret_from_row

        secret_row = self._backend.fetch_one(
            "SELECT * FROM secrets WHERE id = ?", (secret_id,),
        )
        if secret_row is None:
            return False

        secret = secret_from_row(secret_row)
        policies = self.get_policies_for_secret(secret_id)

        for p in policies:
            if p.max_age_seconds is not None:
                last_rotated = secret.last_rotated_at or secret.created_at
                age = (now_utc() - last_rotated).total_seconds()
                if age > p.max_age_seconds:
                    return True
        return False

    def list_policies(
        self,
        *,
        classification: str | None = None,
        limit: int = 100,
    ) -> list[SecretPolicy]:
        if classification is not None:
            rows = self._backend.fetch_all(
                "SELECT * FROM secret_policies WHERE classification = ? ORDER BY created_at LIMIT ?",
                (classification, limit),
            )
        else:
            rows = self._backend.fetch_all(
                "SELECT * FROM secret_policies ORDER BY created_at LIMIT ?",
                (limit,),
            )
        return [policy_from_row(r) for r in rows]
