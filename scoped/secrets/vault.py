"""Secret vault — create, rotate, ref management, and resolution.

The vault is the central entry point for all secret operations.
It coordinates encryption, storage, ref management, and access logging.
"""

from __future__ import annotations

import json
from typing import Any

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

        self._backend.execute(
            """INSERT INTO secrets
               (id, name, description, owner_id, object_id, current_version,
                classification, created_at, expires_at, lifecycle)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, name, description, owner_id, object_id, 1,
             classification, ts.isoformat(),
             expires_at.isoformat() if expires_at else None, "ACTIVE"),
        )

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
        self._backend.execute(
            """INSERT INTO secret_versions
               (id, secret_id, version, encrypted_value, encryption_algo,
                key_id, created_at, created_by, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (vid, sid, 1, encrypted, self._encryption.algorithm,
             use_key, ts.isoformat(), owner_id, "initial"),
        )

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
        row = self._backend.fetch_one(
            "SELECT * FROM secrets WHERE id = ?", (secret_id,),
        )
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
        clauses: list[str] = []
        params: list[Any] = []
        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        if classification is not None:
            clauses.append("classification = ?")
            params.append(classification)
        if active_only:
            clauses.append("lifecycle = 'ACTIVE'")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = self._backend.fetch_all(
            f"SELECT * FROM secrets{where} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        return [secret_from_row(r) for r in rows]

    def archive_secret(self, secret_id: str, *, actor_id: str) -> None:
        """Archive a secret and revoke all its refs."""
        self._backend.execute(
            "UPDATE secrets SET lifecycle = 'ARCHIVED' WHERE id = ?",
            (secret_id,),
        )
        self._backend.execute(
            "UPDATE secret_refs SET lifecycle = 'REVOKED' WHERE secret_id = ?",
            (secret_id,),
        )
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
        self._backend.execute(
            """INSERT INTO secret_versions
               (id, secret_id, version, encrypted_value, encryption_algo,
                key_id, created_at, created_by, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (vid, secret_id, new_version, encrypted, self._encryption.algorithm,
             use_key, ts.isoformat(), rotated_by, reason),
        )
        self._backend.execute(
            "UPDATE secrets SET current_version = ?, last_rotated_at = ? WHERE id = ?",
            (new_version, ts.isoformat(), secret_id),
        )

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
        rows = self._backend.fetch_all(
            "SELECT * FROM secret_versions WHERE secret_id = ? ORDER BY version DESC",
            (secret_id,),
        )
        return [version_from_row(r) for r in rows]

    def get_version(self, secret_id: str, version: int) -> SecretVersion | None:
        row = self._backend.fetch_one(
            "SELECT * FROM secret_versions WHERE secret_id = ? AND version = ?",
            (secret_id, version),
        )
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
        self._backend.execute(
            """INSERT INTO secret_refs
               (id, secret_id, ref_token, granted_to, scope_id, environment_id,
                granted_at, granted_by, expires_at, lifecycle)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rid, secret_id, ref_token, granted_to, scope_id, environment_id,
             ts.isoformat(), granted_by,
             expires_at.isoformat() if expires_at else None, "ACTIVE"),
        )

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
        self._backend.execute(
            "UPDATE secret_refs SET lifecycle = 'REVOKED' WHERE id = ?",
            (ref_id,),
        )
        if self._audit is not None:
            self._audit.record(
                actor_id=revoked_by,
                action=ActionType.SECRET_REVOKE,
                target_type="secret_ref",
                target_id=ref_id,
            )

    def get_ref(self, ref_id: str) -> SecretRef | None:
        row = self._backend.fetch_one(
            "SELECT * FROM secret_refs WHERE id = ?", (ref_id,),
        )
        return ref_from_row(row) if row else None

    def get_ref_by_token(self, ref_token: str) -> SecretRef | None:
        row = self._backend.fetch_one(
            "SELECT * FROM secret_refs WHERE ref_token = ?", (ref_token,),
        )
        return ref_from_row(row) if row else None

    def list_refs(
        self,
        secret_id: str,
        *,
        active_only: bool = True,
    ) -> list[SecretRef]:
        if active_only:
            rows = self._backend.fetch_all(
                "SELECT * FROM secret_refs WHERE secret_id = ? AND lifecycle = 'ACTIVE'",
                (secret_id,),
            )
        else:
            rows = self._backend.fetch_all(
                "SELECT * FROM secret_refs WHERE secret_id = ?",
                (secret_id,),
            )
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
        self._backend.execute(
            """INSERT INTO secret_access_log
               (id, secret_id, ref_id, accessor_id, access_type,
                accessed_at, environment_id, scope_id, result)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (aid, secret_id, ref_id, accessor_id, access_type,
             ts.isoformat(), environment_id, scope_id, result.value),
        )

    def get_access_log(
        self,
        secret_id: str,
        *,
        limit: int = 100,
    ) -> list[SecretAccessEntry]:
        rows = self._backend.fetch_all(
            "SELECT * FROM secret_access_log WHERE secret_id = ? ORDER BY accessed_at DESC LIMIT ?",
            (secret_id, limit),
        )
        return [access_entry_from_row(r) for r in rows]
