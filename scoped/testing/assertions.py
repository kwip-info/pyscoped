"""Domain-specific assertion helpers for pyscoped tests.

These functions validate the framework's core invariants in a readable,
reusable way::

    from scoped.testing.assertions import assert_isolated, assert_audit_recorded

    assert_isolated(backend, object_id=doc.id, owner_id=alice.id, other_id=bob.id)
    assert_audit_recorded(backend, actor_id=alice.id, action="CREATE", target_id=doc.id)
"""

from __future__ import annotations

from typing import Any, Callable

from scoped.storage.interface import StorageBackend


def assert_isolated(
    backend: StorageBackend,
    object_id: str,
    owner_id: str,
    other_id: str,
) -> None:
    """Assert that *other_id* cannot see *object_id* (owner-private isolation).

    Verifies that only the owner can read the object, and that a
    different principal gets no result.
    """
    from scoped.objects.manager import ScopedManager

    mgr = ScopedManager(backend)
    owner_view = mgr.get(object_id, principal_id=owner_id)
    assert owner_view is not None, (
        f"Owner {owner_id} should be able to read object {object_id}"
    )

    other_view = mgr.get(object_id, principal_id=other_id)
    assert other_view is None, (
        f"Principal {other_id} should NOT be able to read object {object_id} "
        f"owned by {owner_id}"
    )


def assert_visible(
    backend: StorageBackend,
    object_id: str,
    principal_id: str,
) -> None:
    """Assert that *principal_id* can see *object_id*."""
    from scoped.objects.manager import ScopedManager

    mgr = ScopedManager(backend)
    result = mgr.get(object_id, principal_id=principal_id)
    assert result is not None, (
        f"Principal {principal_id} should be able to read object {object_id}"
    )


def assert_audit_recorded(
    backend: StorageBackend,
    *,
    actor_id: str,
    action: str,
    target_id: str,
) -> None:
    """Assert that an audit trail entry exists matching the criteria."""
    # Action may be stored as lowercase enum value or uppercase name.
    row = backend.fetch_one(
        "SELECT id FROM audit_trail "
        "WHERE actor_id = ? AND LOWER(action) = LOWER(?) AND target_id = ?",
        (actor_id, action, target_id),
    )
    assert row is not None, (
        f"No audit entry found for actor={actor_id}, action={action}, "
        f"target={target_id}"
    )


def assert_version_count(
    backend: StorageBackend,
    object_id: str,
    expected: int,
) -> None:
    """Assert that an object has exactly *expected* versions."""
    row = backend.fetch_one(
        "SELECT COUNT(*) as cnt FROM object_versions WHERE object_id = ?",
        (object_id,),
    )
    actual = row["cnt"] if row else 0
    assert actual == expected, (
        f"Object {object_id}: expected {expected} versions, found {actual}"
    )


def assert_hash_chain_valid(backend: StorageBackend) -> None:
    """Assert that the entire audit hash chain is intact."""
    from scoped.audit.query import AuditQuery

    query = AuditQuery(backend)
    result = query.verify_chain()
    assert result.valid, (
        f"Audit hash chain broken at sequence {result.broken_at_sequence}"
    )


def assert_tombstoned(backend: StorageBackend, object_id: str) -> None:
    """Assert that an object has been soft-deleted (tombstoned)."""
    row = backend.fetch_one(
        "SELECT id FROM tombstones WHERE object_id = ?",
        (object_id,),
    )
    assert row is not None, f"Object {object_id} is not tombstoned"

    obj_row = backend.fetch_one(
        "SELECT lifecycle FROM scoped_objects WHERE id = ?",
        (object_id,),
    )
    assert obj_row is not None and obj_row["lifecycle"] == "ARCHIVED", (
        f"Object {object_id} lifecycle should be ARCHIVED after tombstone"
    )


def assert_access_denied(fn: Callable, *args: Any, **kwargs: Any) -> None:
    """Assert that calling *fn* raises ``AccessDeniedError``.

    Uses plain ``assert`` for pytest introspection.
    """
    from scoped.exceptions import AccessDeniedError

    exc = None
    try:
        fn(*args, **kwargs)
    except AccessDeniedError as e:
        exc = e
    assert exc is not None, (
        f"Expected AccessDeniedError from {fn.__name__}, but no exception was raised"
    )


def assert_can_read(
    backend: StorageBackend,
    object_id: str,
    principal_id: str,
) -> None:
    """Assert that *principal_id* can read *object_id* via ScopedManager."""
    from scoped.objects.manager import ScopedManager

    mgr = ScopedManager(backend)
    result = mgr.get(object_id, principal_id=principal_id)
    assert result is not None, (
        f"Principal {principal_id} should be able to read object {object_id}"
    )


def assert_cannot_read(
    backend: StorageBackend,
    object_id: str,
    principal_id: str,
) -> None:
    """Assert that *principal_id* cannot read *object_id* via ScopedManager."""
    from scoped.objects.manager import ScopedManager

    mgr = ScopedManager(backend)
    result = mgr.get(object_id, principal_id=principal_id)
    assert result is None, (
        f"Principal {principal_id} should NOT be able to read object {object_id}"
    )


def assert_trace_exists(
    backend: StorageBackend,
    *,
    actor_id: str | None = None,
    action: str | None = None,
    target_id: str | None = None,
    target_type: str | None = None,
) -> None:
    """Assert that at least one audit trail entry matches all given criteria.

    More flexible than ``assert_audit_recorded`` — all parameters are optional.
    """
    conditions = []
    params: list[Any] = []
    if actor_id is not None:
        conditions.append("actor_id = ?")
        params.append(actor_id)
    if action is not None:
        conditions.append("LOWER(action) = LOWER(?)")
        params.append(action)
    if target_id is not None:
        conditions.append("target_id = ?")
        params.append(target_id)
    if target_type is not None:
        conditions.append("target_type = ?")
        params.append(target_type)

    where = " AND ".join(conditions) if conditions else "1 = 1"
    row = backend.fetch_one(
        f"SELECT id FROM audit_trail WHERE {where}",
        tuple(params),
    )
    criteria = {
        k: v for k, v in
        [("actor_id", actor_id), ("action", action),
         ("target_id", target_id), ("target_type", target_type)]
        if v is not None
    }
    assert row is not None, f"No audit entry found matching {criteria}"


def assert_secret_never_leaked(
    backend: StorageBackend,
    secret_id: str,
) -> None:
    """Assert that a secret's plaintext never appears in audit trails.

    Checks that no audit entry's ``before_state`` or ``after_state``
    contains the encrypted value (which would indicate a leak).
    """
    version_row = backend.fetch_one(
        "SELECT encrypted_value FROM secret_versions "
        "WHERE secret_id = ? ORDER BY version DESC LIMIT 1",
        (secret_id,),
    )
    if version_row is None:
        return

    encrypted = version_row["encrypted_value"]
    leak = backend.fetch_one(
        "SELECT id FROM audit_trail "
        "WHERE (before_state LIKE ? OR after_state LIKE ?)",
        (f"%{encrypted}%", f"%{encrypted}%"),
    )
    assert leak is None, (
        f"Secret {secret_id} encrypted value found in audit trail entry {leak['id']}"
    )
