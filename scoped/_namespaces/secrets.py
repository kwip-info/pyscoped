"""Secrets namespace — encrypted vault with zero-trust access.

Secrets are the tightest isolation boundary. Values are encrypted at
rest, accessed only through opaque reference tokens, and never appear
in audit trails, snapshots, or logs.

Usage::

    import scoped

    with scoped.as_principal(alice):
        secret, v1 = scoped.secrets.create("api-key", "sk-12345")

        # Grant Bob access via a ref token
        ref = scoped.secrets.grant_ref(secret.id, bob)

    # Bob resolves the ref to get the plaintext (access is logged)
    with scoped.as_principal(bob):
        value = scoped.secrets.resolve(ref.ref_token)

Every access attempt is logged. Denied attempts are logged too.
"""

from __future__ import annotations

from typing import Any

from scoped._namespaces._base import _resolve_principal_id, _to_id


class SecretsNamespace:
    """Simplified API for secret management.

    Wraps ``SecretVault`` from Layer 11 with context-aware defaults.

    Key methods:
        - ``create(name, value)`` — create an encrypted secret
        - ``rotate(secret_id, new_value=...)`` — rotate to a new value
        - ``grant_ref(secret_id, principal)`` — grant access via ref token
        - ``resolve(ref_token)`` — decrypt and return the plaintext value
    """

    def __init__(self, services: Any) -> None:
        self._svc = services

    def create(
        self,
        name: str,
        value: str,
        *,
        owner_id: str | None = None,
        description: str = "",
        classification: str = "standard",
    ) -> tuple[Any, Any]:
        """Create an encrypted secret.

        The plaintext value is immediately encrypted and stored. It
        never appears in audit trails or logs.

        Args:
            name: Human-readable name (e.g. ``"api-key"``).
            value: The plaintext secret value to encrypt.
            owner_id: The owning principal. If omitted, inferred from context.
            description: Optional description.
            classification: ``"standard"``, ``"sensitive"``, or
                            ``"critical"``. Affects policy enforcement.

        Returns:
            A tuple of ``(Secret, SecretVersion)``.

        Example::

            with client.as_principal(alice):
                secret, v1 = client.secrets.create(
                    "stripe-key", "sk_live_...", classification="critical"
                )
        """
        owner = _resolve_principal_id(owner_id)
        return self._svc.secrets.create_secret(
            name=name,
            plaintext_value=value,
            owner_id=owner,
            description=description,
            classification=classification,
        )

    def rotate(
        self,
        secret_id: str,
        *,
        new_value: str,
        rotated_by: str | None = None,
        reason: str = "rotation",
    ) -> Any:
        """Rotate a secret to a new value.

        Creates a new version with the new encrypted value. The old
        version is preserved but the secret now points to the new one.

        Args:
            secret_id: The secret to rotate.
            new_value: The new plaintext value.
            rotated_by: Who is rotating. If omitted, inferred from context.
            reason: Human-readable reason for rotation.

        Returns:
            The new ``SecretVersion``.
        """
        actor = _resolve_principal_id(rotated_by)
        return self._svc.secrets.rotate(
            secret_id,
            new_value=new_value,
            rotated_by=actor,
            reason=reason,
        )

    def grant_ref(
        self,
        secret_id: str,
        principal: Any,
        *,
        granted_by: str | None = None,
        scope_id: str | None = None,
        environment_id: str | None = None,
        expires_at: Any | None = None,
    ) -> Any:
        """Grant a principal access to a secret via a ref token.

        The ref token is an opaque string that the grantee uses to
        resolve (decrypt) the secret. Access can be scoped to a specific
        scope or environment, and can have an expiry.

        Args:
            secret_id: The secret to grant access to.
            principal: The grantee (``Principal`` object or string ID).
            granted_by: Who is granting. If omitted, inferred from context.
            scope_id: Restrict the ref to this scope (optional).
            environment_id: Restrict to this environment (optional).
            expires_at: Expiry datetime (optional).

        Returns:
            A ``SecretRef`` with ``.ref_token`` for the grantee to use.
        """
        actor = _resolve_principal_id(granted_by)
        return self._svc.secrets.grant_ref(
            secret_id=secret_id,
            granted_to=_to_id(principal),
            granted_by=actor,
            scope_id=scope_id,
            environment_id=environment_id,
            expires_at=expires_at,
        )

    def resolve(
        self,
        ref_token: str,
        *,
        accessor_id: str | None = None,
        scope_id: str | None = None,
        environment_id: str | None = None,
    ) -> str:
        """Resolve a ref token to the secret's plaintext value.

        Validates that the accessor has permission, the ref is not
        expired, and any scope/environment restrictions are met. Every
        attempt (success or failure) is logged.

        Args:
            ref_token: The opaque token received from ``grant_ref()``.
            accessor_id: Who is accessing. If omitted, inferred from context.
            scope_id: The scope context for this access (optional).
            environment_id: The environment context (optional).

        Returns:
            The decrypted plaintext value.

        Raises:
            SecretAccessDeniedError: If access is denied.
            SecretRefExpiredError: If the ref has expired.
        """
        accessor = _resolve_principal_id(accessor_id)
        return self._svc.secrets.resolve(
            ref_token,
            accessor_id=accessor,
            scope_id=scope_id,
            environment_id=environment_id,
        )
