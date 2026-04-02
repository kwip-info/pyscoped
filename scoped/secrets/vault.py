"""Secret vault — create, rotate, ref management, and resolution.

The vault is the central entry point for all secret operations.
It coordinates encryption, storage, ref management, and access logging.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from scoped.exceptions import (
    SecretAccessDeniedError,
    SecretNotFoundError,
    SecretRefExpiredError,
)
from scoped.objects.manager import ScopedManager
from scoped.secrets.backend import SecretBackend
from scoped.secrets.models import (
    AccessResult,
    Secret,
    SecretAccessEntry,
    SecretRef,
    SecretVersion,
    access_entry_from_row,
    ref_from_row,
    secret_from_row,
    version_from_row,
)
from scoped.storage._query import compile_for
from scoped.storage._schema import (
    secret_access_log,
    secret_refs,
    secret_versions,
    secrets,
)
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, Lifecycle, generate_id, now_utc


class SecretVault:
    """Create, rotate, and resolve secrets via encrypted refs."""

    def __init__(
        self,
        backend: StorageBackend,
        encryption: SecretBackend,
        *,
        object_manager: ScopedManager | None = None,
        audit_writer: Any | None = None,
    ) -> None:
        self._backend = backend
        self._encryption = encryption
        self._objects = object_manager
        self._audit = audit_writer
        # Generate a default key on init
        self._default_key_id, _ = encryption.generate_key()

    # -- Secret CRUD -------------------------------------------------------

    def create_secret(
        self,
        *,
        name: str,
        plaintext_value: str,
        owner_id: str,
        description: str = "",
        classification: str = "standard",
        expires_at: Any | None = None,
        key_id: str | None = None,
    ) -> tuple[Secret, SecretVersion]:
        """Create a new secret with its first encrypted version.

        Also creates a backing scoped object if an object_manager
        is configured.
        """
        ts = now_utc()
        sid = generate_id()
        use_key = key_id or self._default_key_id

        # Create backing scoped object
        if self._objects is not None:
            obj, _ = self._objects.create(
                object_type="secret",
                owner_id=owner_id,
                data={"name": name, "classification": classification},
            )
            object_id = obj.id
        else:
            object_id = generate_id()

        # Encrypt value
        encrypted = self._encryption.encrypt(plaintext_value, key_id=use_key)

        secret = Secret(
            id=sid,
            name=name,
            owner_id=owner_id,
            object_id=object_id,
            description=description,
            classification=__import__("scoped.secrets.models", fromlist=["SecretClassification"]).SecretClassification(classification),
            created_at=ts,
            expires_at=expires_at,
        )

        stmt = sa.insert(secrets).values(
            id=sid,
            name=name,
            description=description,
            owner_id=owner_id,
            object_id=object_id,
            current_version=1,
            classification=classification,
            created_at=ts.isoformat(),
            expires_at=expires_at.isoformat() if expires_at else None,
            lifecycle="ACTIVE",
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        # Create first version
        vid = generate_id()
        version = SecretVersion(
            id=vid,
            secret_id=sid,
            version=1,
            encrypted_value=encrypted,
            encryption_algo=self._encryption.algorithm,
            key_id=use_key,
            created_at=ts,
            created_by=owner_id,
            reason="initial",
        )
        stmt = sa.insert(secret_versions).values(
            id=vid,
            secret_id=sid,
            version=1,
            encrypted_value=encrypted,
            encryption_algo=self._encryption.algorithm,
            key_id=use_key,
            created_at=ts.isoformat(),
            created_by=owner_id,
            reason="initial",
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit is not None:
            self._audit.record(
                actor_id=owner_id,
                action=ActionType.SECRET_CREATE,
                target_type="secret",
                target_id=sid,
                after_state=secret.snapshot(),
            )

        return secret, version

    def get_secret(self, secret_id: str) -> Secret | None:
        stmt = sa.select(secrets).where(secrets.c.id == secret_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return secret_from_row(row) if row else None

    def get_secret_or_raise(self, secret_id: str) -> Secret:
        s = self.get_secret(secret_id)
        if s is None:
            raise SecretNotFoundError(
                f"Secret {secret_id} not found",
                context={"secret_id": secret_id},
            )
        return s

    def list_secrets(
        self,
        *,
        owner_id: str | None = None,
        classification: str | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[Secret]:
        stmt = sa.select(secrets)
        if owner_id is not None:
            stmt = stmt.where(secrets.c.owner_id == owner_id)
        if classification is not None:
            stmt = stmt.where(secrets.c.classification == classification)
        if active_only:
            stmt = stmt.where(secrets.c.lifecycle == "ACTIVE")
        stmt = stmt.order_by(secrets.c.created_at.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [secret_from_row(r) for r in rows]

    def archive_secret(self, secret_id: str, *, actor_id: str) -> None:
        """Archive a secret and revoke all its refs."""
        stmt = sa.update(secrets).where(secrets.c.id == secret_id).values(
            lifecycle="ARCHIVED",
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        stmt = sa.update(secret_refs).where(
            secret_refs.c.secret_id == secret_id,
        ).values(lifecycle="REVOKED")
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit is not None:
            self._audit.record(
                actor_id=actor_id,
                action=ActionType.SECRET_REVOKE,
                target_type="secret",
                target_id=secret_id,
            )

    # -- Rotation ----------------------------------------------------------

    def rotate(
        self,
        secret_id: str,
        *,
        new_value: str,
        rotated_by: str,
        reason: str = "rotation",
        key_id: str | None = None,
    ) -> SecretVersion:
        """Rotate a secret to a new value. Old version is kept."""
        secret = self.get_secret_or_raise(secret_id)
        ts = now_utc()
        use_key = key_id or self._default_key_id
        new_version = secret.current_version + 1

        encrypted = self._encryption.encrypt(new_value, key_id=use_key)

        vid = generate_id()
        version = SecretVersion(
            id=vid,
            secret_id=secret_id,
            version=new_version,
            encrypted_value=encrypted,
            encryption_algo=self._encryption.algorithm,
            key_id=use_key,
            created_at=ts,
            created_by=rotated_by,
            reason=reason,
        )
        stmt = sa.insert(secret_versions).values(
            id=vid,
            secret_id=secret_id,
            version=new_version,
            encrypted_value=encrypted,
            encryption_algo=self._encryption.algorithm,
            key_id=use_key,
            created_at=ts.isoformat(),
            created_by=rotated_by,
            reason=reason,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        stmt = sa.update(secrets).where(secrets.c.id == secret_id).values(
            current_version=new_version,
            last_rotated_at=ts.isoformat(),
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit is not None:
            self._audit.record(
                actor_id=rotated_by,
                action=ActionType.SECRET_ROTATE,
                target_type="secret",
                target_id=secret_id,
                after_state=version.snapshot(),
            )

        return version

    def get_versions(self, secret_id: str) -> list[SecretVersion]:
        stmt = sa.select(secret_versions).where(
            secret_versions.c.secret_id == secret_id,
        ).order_by(secret_versions.c.version.desc())
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [version_from_row(r) for r in rows]

    def get_version(self, secret_id: str, version: int) -> SecretVersion | None:
        stmt = sa.select(secret_versions).where(
            (secret_versions.c.secret_id == secret_id)
            & (secret_versions.c.version == version),
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return version_from_row(row) if row else None

    # -- Refs --------------------------------------------------------------

    def grant_ref(
        self,
        *,
        secret_id: str,
        granted_to: str,
        granted_by: str,
        scope_id: str | None = None,
        environment_id: str | None = None,
        expires_at: Any | None = None,
    ) -> SecretRef:
        """Grant a reference token for a secret to a principal."""
        self.get_secret_or_raise(secret_id)
        ts = now_utc()
        rid = generate_id()
        ref_token = generate_id()  # opaque token

        ref = SecretRef(
            id=rid,
            secret_id=secret_id,
            ref_token=ref_token,
            granted_to=granted_to,
            scope_id=scope_id,
            environment_id=environment_id,
            granted_at=ts,
            granted_by=granted_by,
            expires_at=expires_at,
        )
        stmt = sa.insert(secret_refs).values(
            id=rid,
            secret_id=secret_id,
            ref_token=ref_token,
            granted_to=granted_to,
            scope_id=scope_id,
            environment_id=environment_id,
            granted_at=ts.isoformat(),
            granted_by=granted_by,
            expires_at=expires_at.isoformat() if expires_at else None,
            lifecycle="ACTIVE",
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit is not None:
            self._audit.record(
                actor_id=granted_by,
                action=ActionType.SECRET_REF_GRANT,
                target_type="secret_ref",
                target_id=rid,
                after_state=ref.snapshot(),
            )

        return ref

    def revoke_ref(self, ref_id: str, *, revoked_by: str) -> None:
        """Immediately revoke a secret ref."""
        stmt = sa.update(secret_refs).where(secret_refs.c.id == ref_id).values(
            lifecycle="REVOKED",
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

        if self._audit is not None:
            self._audit.record(
                actor_id=revoked_by,
                action=ActionType.SECRET_REVOKE,
                target_type="secret_ref",
                target_id=ref_id,
            )

    def get_ref(self, ref_id: str) -> SecretRef | None:
        stmt = sa.select(secret_refs).where(secret_refs.c.id == ref_id)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return ref_from_row(row) if row else None

    def get_ref_by_token(self, ref_token: str) -> SecretRef | None:
        stmt = sa.select(secret_refs).where(secret_refs.c.ref_token == ref_token)
        sql, params = compile_for(stmt, self._backend.dialect)
        row = self._backend.fetch_one(sql, params)
        return ref_from_row(row) if row else None

    def list_refs(
        self,
        secret_id: str,
        *,
        active_only: bool = True,
    ) -> list[SecretRef]:
        stmt = sa.select(secret_refs).where(secret_refs.c.secret_id == secret_id)
        if active_only:
            stmt = stmt.where(secret_refs.c.lifecycle == "ACTIVE")
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [ref_from_row(r) for r in rows]

    # -- Resolution --------------------------------------------------------

    def resolve(
        self,
        ref_token: str,
        *,
        accessor_id: str,
        scope_id: str | None = None,
        environment_id: str | None = None,
    ) -> str:
        """Resolve a ref token to the plaintext secret value.

        Checks: ref is active, not expired, accessor matches granted_to,
        scope/environment match (if restricted).

        Every attempt is logged.
        """
        ref = self.get_ref_by_token(ref_token)
        if ref is None:
            # Can't log to access_log without a valid secret_id (FK constraint)
            raise SecretAccessDeniedError(
                "Invalid ref token",
                context={"accessor_id": accessor_id},
            )

        # Check ref is active
        if not ref.is_active:
            self._log_access(
                secret_id=ref.secret_id, ref_id=ref.id,
                accessor_id=accessor_id, access_type="resolve",
                result=AccessResult.DENIED,
                scope_id=scope_id, environment_id=environment_id,
            )
            raise SecretAccessDeniedError(
                "Ref has been revoked",
                context={"ref_id": ref.id, "accessor_id": accessor_id},
            )

        # Check expiry
        if ref.is_expired(now_utc()):
            self._log_access(
                secret_id=ref.secret_id, ref_id=ref.id,
                accessor_id=accessor_id, access_type="resolve",
                result=AccessResult.EXPIRED,
                scope_id=scope_id, environment_id=environment_id,
            )
            raise SecretRefExpiredError(
                "Ref has expired",
                context={"ref_id": ref.id, "expires_at": ref.expires_at.isoformat()},
            )

        # Check accessor matches granted_to
        if ref.granted_to != accessor_id:
            self._log_access(
                secret_id=ref.secret_id, ref_id=ref.id,
                accessor_id=accessor_id, access_type="resolve",
                result=AccessResult.DENIED,
                scope_id=scope_id, environment_id=environment_id,
            )
            raise SecretAccessDeniedError(
                "Accessor does not match ref grant",
                context={"ref_id": ref.id, "accessor_id": accessor_id},
            )

        # Check scope restriction
        if ref.scope_id is not None and scope_id != ref.scope_id:
            self._log_access(
                secret_id=ref.secret_id, ref_id=ref.id,
                accessor_id=accessor_id, access_type="resolve",
                result=AccessResult.DENIED,
                scope_id=scope_id, environment_id=environment_id,
            )
            raise SecretAccessDeniedError(
                "Ref is restricted to a different scope",
                context={"ref_id": ref.id, "required_scope": ref.scope_id},
            )

        # Check environment restriction
        if ref.environment_id is not None and environment_id != ref.environment_id:
            self._log_access(
                secret_id=ref.secret_id, ref_id=ref.id,
                accessor_id=accessor_id, access_type="resolve",
                result=AccessResult.DENIED,
                scope_id=scope_id, environment_id=environment_id,
            )
            raise SecretAccessDeniedError(
                "Ref is restricted to a different environment",
                context={"ref_id": ref.id, "required_env": ref.environment_id},
            )

        # Get current version and decrypt
        secret = self.get_secret_or_raise(ref.secret_id)
        ver = self.get_version(ref.secret_id, secret.current_version)
        if ver is None:
            raise SecretNotFoundError(
                f"Version {secret.current_version} not found for secret {ref.secret_id}",
            )

        plaintext = self._encryption.decrypt(ver.encrypted_value, key_id=ver.key_id)

        # Log successful access
        self._log_access(
            secret_id=ref.secret_id, ref_id=ref.id,
            accessor_id=accessor_id, access_type="resolve",
            result=AccessResult.SUCCESS,
            scope_id=scope_id, environment_id=environment_id,
        )

        if self._audit is not None:
            self._audit.record(
                actor_id=accessor_id,
                action=ActionType.SECRET_REF_RESOLVE,
                target_type="secret",
                target_id=ref.secret_id,
                # NOTE: never log the plaintext value
            )

        return plaintext

    # -- Access log --------------------------------------------------------

    def _log_access(
        self,
        *,
        secret_id: str,
        ref_id: str | None,
        accessor_id: str,
        access_type: str,
        result: AccessResult,
        scope_id: str | None = None,
        environment_id: str | None = None,
    ) -> None:
        ts = now_utc()
        aid = generate_id()
        stmt = sa.insert(secret_access_log).values(
            id=aid,
            secret_id=secret_id,
            ref_id=ref_id,
            accessor_id=accessor_id,
            access_type=access_type,
            accessed_at=ts.isoformat(),
            environment_id=environment_id,
            scope_id=scope_id,
            result=result.value,
        )
        sql, params = compile_for(stmt, self._backend.dialect)
        self._backend.execute(sql, params)

    def get_access_log(
        self,
        secret_id: str,
        *,
        limit: int = 100,
    ) -> list[SecretAccessEntry]:
        stmt = sa.select(secret_access_log).where(
            secret_access_log.c.secret_id == secret_id,
        ).order_by(secret_access_log.c.accessed_at.desc()).limit(limit)
        sql, params = compile_for(stmt, self._backend.dialect)
        rows = self._backend.fetch_all(sql, params)
        return [access_entry_from_row(r) for r in rows]
