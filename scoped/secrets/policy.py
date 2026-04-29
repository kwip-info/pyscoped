"""Secret policy management — rotation, access restrictions."""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from scoped._stability import stable
from scoped.secrets.models import (
    SecretClassification,
    SecretPolicy,
    policy_from_row,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import secret_policies, secrets
from scoped.storage.interface import StorageBackend
from scoped.types import generate_id, now_utc


def _coerce_classification(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return SecretClassification(value).value
    except ValueError as exc:
        valid = ", ".join(item.value for item in SecretClassification)
        raise ValueError(f"classification must be one of: {valid}") from exc


def _validate_id_list(values: list[str] | None, *, field_name: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError(f"{field_name} must be a list of string IDs")
    for value in values:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field_name} entries must be non-empty strings")
    return values


@stable(since="1.5.0")
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
        if secret_id is None and classification is None:
            raise ValueError("policy must target a secret_id or classification")
        if max_age_seconds is not None and max_age_seconds < 0:
            raise ValueError("max_age_seconds must be >= 0")
        normalized_classification = _coerce_classification(classification)
        scope_restrictions = _validate_id_list(
            allowed_scopes,
            field_name="allowed_scopes",
        )
        env_restrictions = _validate_id_list(
            allowed_envs,
            field_name="allowed_envs",
        )
        if secret_id is not None:
            stmt = sa.select(secrets.c.id).where(secrets.c.id == secret_id)
            sql, params = compile_for(stmt, self._backend.dialect)
            if self._backend.fetch_one(sql, params) is None:
                raise ValueError(f"secret_id does not exist: {secret_id}")

        ts = now_utc()
        pid = generate_id()
        policy = SecretPolicy(
            id=pid,
            secret_id=secret_id,
            classification=normalized_classification,
            max_age_seconds=max_age_seconds,
            auto_rotate=auto_rotate,
            allowed_scopes=scope_restrictions,
            allowed_envs=env_restrictions,
            created_at=ts,
            created_by=created_by,
        )
        stmt = sa.insert(secret_policies).values(
            id=pid,
            secret_id=secret_id,
            classification=normalized_classification,
            max_age_seconds=max_age_seconds,
            auto_rotate=int(auto_rotate),
            allowed_scopes=json.dumps(policy.allowed_scopes),
            allowed_envs=json.dumps(policy.allowed_envs),
            created_at=ts.isoformat(),
            created_by=created_by,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)
        return policy

    def get_policy(self, policy_id: str) -> SecretPolicy | None:
        stmt = sa.select(secret_policies).where(secret_policies.c.id == policy_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return policy_from_row(row) if row else None

    def get_policies_for_secret(self, secret_id: str) -> list[SecretPolicy]:
        """Get policies that apply to a specific secret (by id or classification)."""
        # Get secret's classification
        stmt = sa.select(secrets.c.classification).where(secrets.c.id == secret_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        secret_row = self._backend.fetch_one(sql, params)
        if secret_row is None:
            return []

        stmt = sa.select(secret_policies).where(
            (secret_policies.c.secret_id == secret_id)
            | (secret_policies.c.classification == secret_row["classification"])
        ).order_by(secret_policies.c.created_at)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
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

        stmt = sa.select(secrets).where(secrets.c.id == secret_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        secret_row = self._backend.fetch_one(sql, params)
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
        stmt = sa.select(secret_policies)
        if classification is not None:
            stmt = stmt.where(secret_policies.c.classification == classification)
        stmt = stmt.order_by(secret_policies.c.created_at).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [policy_from_row(r) for r in rows]
