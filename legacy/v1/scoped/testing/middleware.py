"""Runtime compliance middleware.

Enforces compliance invariants during operation:
1. Context enforcement — every operation has a principal
2. Trace completeness — every mutation produces a trace
3. Version integrity — every save creates a new version
4. Secret leak detection — no plaintext secrets in audit states
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scoped.audit.query import AuditQuery
from scoped.exceptions import ComplianceViolation
from scoped.identity.context import ScopedContext
from scoped.storage.interface import StorageBackend
from scoped.types import ActionType, now_utc


@dataclass(frozen=True, slots=True)
class ComplianceCheck:
    """A recorded compliance check."""

    check_type: str
    passed: bool
    detail: str = ""


class ComplianceMiddleware:
    """Runtime compliance enforcement.

    Wraps operations to verify invariants are maintained.
    Records checks and can raise ComplianceViolation on failure.
    """

    def __init__(
        self,
        backend: StorageBackend,
        *,
        raise_on_violation: bool = True,
    ) -> None:
        self._backend = backend
        self._query = AuditQuery(backend)
        self._raise = raise_on_violation
        self._checks: list[ComplianceCheck] = []

    @property
    def checks(self) -> list[ComplianceCheck]:
        """All recorded compliance checks."""
        return list(self._checks)

    @property
    def violations(self) -> list[ComplianceCheck]:
        """Failed compliance checks."""
        return [c for c in self._checks if not c.passed]

    def reset(self) -> None:
        """Clear all recorded checks."""
        self._checks.clear()

    def enforce_context(self) -> ComplianceCheck:
        """Verify a ScopedContext is currently active.

        Raises ComplianceViolation if no context and raise_on_violation is True.
        """
        ctx = ScopedContext.current_or_none()
        if ctx is not None:
            check = ComplianceCheck(
                check_type="context_enforcement",
                passed=True,
                detail=f"Active context: principal={ctx.principal_id}",
            )
        else:
            check = ComplianceCheck(
                check_type="context_enforcement",
                passed=False,
                detail="No ScopedContext active",
            )
            if self._raise:
                self._checks.append(check)
                raise ComplianceViolation(
                    "Operation attempted without ScopedContext",
                    context={"check_type": "context_enforcement"},
                )

        self._checks.append(check)
        return check

    def enforce_trace(
        self,
        *,
        actor_id: str,
        action: ActionType,
        target_id: str,
    ) -> ComplianceCheck:
        """Verify that a trace entry exists for the given operation.

        Should be called after a mutation to verify the trace was recorded.
        """
        entries = self._query.query(
            actor_id=actor_id,
            action=action,
            target_id=target_id,
            limit=1,
        )
        if entries:
            check = ComplianceCheck(
                check_type="trace_completeness",
                passed=True,
                detail=f"Trace found: {entries[0].id}",
            )
        else:
            check = ComplianceCheck(
                check_type="trace_completeness",
                passed=False,
                detail=f"No trace for actor={actor_id} action={action.value} target={target_id}",
            )
            if self._raise:
                self._checks.append(check)
                raise ComplianceViolation(
                    f"Mutation completed without trace: {action.value} on {target_id}",
                    context={
                        "check_type": "trace_completeness",
                        "actor_id": actor_id,
                        "action": action.value,
                        "target_id": target_id,
                    },
                )

        self._checks.append(check)
        return check

    def enforce_version_integrity(
        self,
        object_id: str,
        *,
        expected_version: int,
    ) -> ComplianceCheck:
        """Verify an object's version matches expected after mutation."""
        row = self._backend.fetch_one(
            "SELECT current_version FROM scoped_objects WHERE id = ?",
            (object_id,),
        )
        if row is None:
            check = ComplianceCheck(
                check_type="version_integrity",
                passed=False,
                detail=f"Object {object_id} not found",
            )
        elif row["current_version"] == expected_version:
            check = ComplianceCheck(
                check_type="version_integrity",
                passed=True,
                detail=f"Version matches: {expected_version}",
            )
        else:
            check = ComplianceCheck(
                check_type="version_integrity",
                passed=False,
                detail=(
                    f"Expected version {expected_version}, "
                    f"got {row['current_version']}"
                ),
            )
            if self._raise:
                self._checks.append(check)
                raise ComplianceViolation(
                    f"Version integrity violation: object {object_id} "
                    f"expected v{expected_version}, got v{row['current_version']}",
                    context={
                        "check_type": "version_integrity",
                        "object_id": object_id,
                        "expected": expected_version,
                        "actual": row["current_version"],
                    },
                )

        self._checks.append(check)
        return check

    def enforce_secret_not_in_state(
        self,
        state: dict[str, Any] | None,
        *,
        known_secret_values: list[str] | None = None,
    ) -> ComplianceCheck:
        """Check that no known secret values appear in a state dict.

        Scans the serialized state for any known plaintext secret values.
        """
        if state is None or not known_secret_values:
            check = ComplianceCheck(
                check_type="secret_leak_detection",
                passed=True,
                detail="Nothing to check",
            )
            self._checks.append(check)
            return check

        import json
        state_str = json.dumps(state)

        for secret_value in known_secret_values:
            if secret_value in state_str:
                check = ComplianceCheck(
                    check_type="secret_leak_detection",
                    passed=False,
                    detail="Plaintext secret value found in state data",
                )
                if self._raise:
                    self._checks.append(check)
                    raise ComplianceViolation(
                        "Secret leak detected: plaintext value in state data",
                        context={"check_type": "secret_leak_detection"},
                    )
                self._checks.append(check)
                return check

        check = ComplianceCheck(
            check_type="secret_leak_detection",
            passed=True,
            detail=f"Checked {len(known_secret_values)} secret values — clean",
        )
        self._checks.append(check)
        return check

    def enforce_revocation(
        self,
        *,
        principal_id: str,
        object_id: str,
        manager: Any,
    ) -> ComplianceCheck:
        """Verify that a revoked principal cannot access an object.

        Should be called immediately after revoking access.
        """
        result = manager.get(object_id, principal_id=principal_id)
        if result is None:
            check = ComplianceCheck(
                check_type="revocation_immediacy",
                passed=True,
                detail=f"Principal {principal_id} correctly denied access to {object_id}",
            )
        else:
            check = ComplianceCheck(
                check_type="revocation_immediacy",
                passed=False,
                detail=f"Principal {principal_id} still has access to {object_id} after revocation",
            )
            if self._raise:
                self._checks.append(check)
                raise ComplianceViolation(
                    f"Revocation not immediate: {principal_id} still has access to {object_id}",
                    context={
                        "check_type": "revocation_immediacy",
                        "principal_id": principal_id,
                        "object_id": object_id,
                    },
                )

        self._checks.append(check)
        return check
