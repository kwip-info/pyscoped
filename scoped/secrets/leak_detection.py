"""Leak detection — detect plaintext secret values in non-secret contexts.

Monitors for secret values appearing where they shouldn't:
- Audit trail before_state/after_state
- Environment snapshots
- Object version data
"""

from __future__ import annotations

from typing import Any

from scoped.exceptions import SecretLeakDetectedError
from scoped.secrets.backend import SecretBackend
from scoped.secrets.models import version_from_row
from scoped.storage.interface import StorageBackend


class LeakDetector:
    """Detect plaintext secret values in arbitrary data."""

    def __init__(
        self,
        backend: StorageBackend,
        encryption: SecretBackend,
    ) -> None:
        self._backend = backend
        self._encryption = encryption

    def get_known_values(self) -> set[str]:
        """Decrypt all current secret values for comparison.

        Returns the set of plaintext values. This is expensive and
        should be used sparingly (e.g., in compliance checks, not
        on every operation).
        """
        values: set[str] = set()
        rows = self._backend.fetch_all(
            """SELECT sv.* FROM secret_versions sv
               JOIN secrets s ON sv.secret_id = s.id AND sv.version = s.current_version
               WHERE s.lifecycle = 'ACTIVE'""",
        )
        for row in rows:
            ver = version_from_row(row)
            try:
                plaintext = self._encryption.decrypt(
                    ver.encrypted_value, key_id=ver.key_id,
                )
                values.add(plaintext)
            except Exception:
                pass  # skip values we can't decrypt (different key)
        return values

    def scan_data(
        self,
        data: dict[str, Any],
        *,
        known_values: set[str] | None = None,
    ) -> list[str]:
        """Scan a data dict for leaked secret values.

        Returns list of field paths where leaks were found.
        """
        if known_values is None:
            known_values = self.get_known_values()
        if not known_values:
            return []
        return self._scan_recursive(data, known_values, prefix="")

    def _scan_recursive(
        self,
        obj: Any,
        known_values: set[str],
        prefix: str,
    ) -> list[str]:
        leaks: list[str] = []
        if isinstance(obj, str):
            for val in known_values:
                if val and val in obj:
                    leaks.append(prefix or "<root>")
                    break
        elif isinstance(obj, dict):
            for key, value in obj.items():
                path = f"{prefix}.{key}" if prefix else key
                leaks.extend(self._scan_recursive(value, known_values, path))
        elif isinstance(obj, (list, tuple)):
            for i, item in enumerate(obj):
                path = f"{prefix}[{i}]"
                leaks.extend(self._scan_recursive(item, known_values, path))
        return leaks

    def scan_or_raise(
        self,
        data: dict[str, Any],
        *,
        context: str = "",
        known_values: set[str] | None = None,
    ) -> None:
        """Scan data and raise if any leaks found."""
        leaks = self.scan_data(data, known_values=known_values)
        if leaks:
            raise SecretLeakDetectedError(
                f"Secret value detected in {context or 'data'} at: {', '.join(leaks)}",
                context={"leaked_fields": leaks, "context": context},
            )
