"""Typed identifiers for pyscoped entities.

Thin ``str`` subclasses that provide static type safety without any
runtime overhead.  ``isinstance(PrincipalId("x"), str)`` is ``True``,
so typed IDs are fully backward compatible with code that expects
plain strings.

Usage::

    from scoped.ids import PrincipalId, ObjectId

    pid = PrincipalId.generate()   # type: PrincipalId (is-a str)
    oid = ObjectId.generate()      # type: ObjectId  (is-a str)

    # These would be caught by mypy/pyright:
    # get_principal(oid)  # error: ObjectId is not PrincipalId
"""

from __future__ import annotations

import uuid


class ScopedId(str):
    """Base class for typed identifiers.

    Inherits ``str`` so all existing code that expects ``str`` continues
    to work.  The subclass type gives static analysers enough information
    to catch cross-ID mistakes.
    """

    __slots__ = ()

    @classmethod
    def generate(cls) -> ScopedId:
        """Generate a new random ID (UUID4 hex, 32 characters)."""
        return cls(uuid.uuid4().hex)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({super().__repr__()})"


# -- Layer 1: Registry -------------------------------------------------------

class EntryId(ScopedId):
    """ID for a RegistryEntry."""
    __slots__ = ()


# -- Layer 2: Identity -------------------------------------------------------

class PrincipalId(ScopedId):
    """ID for a Principal."""
    __slots__ = ()


# -- Layer 3: Objects --------------------------------------------------------

class ObjectId(ScopedId):
    """ID for a ScopedObject."""
    __slots__ = ()


class VersionId(ScopedId):
    """ID for an ObjectVersion."""
    __slots__ = ()


# -- Layer 4: Tenancy --------------------------------------------------------

class ScopeId(ScopedId):
    """ID for a Scope."""
    __slots__ = ()


class MembershipId(ScopedId):
    """ID for a ScopeMembership."""
    __slots__ = ()


class ProjectionId(ScopedId):
    """ID for a ScopeProjection."""
    __slots__ = ()


# -- Layer 5: Rules ----------------------------------------------------------

class RuleId(ScopedId):
    """ID for a Rule."""
    __slots__ = ()


class BindingId(ScopedId):
    """ID for a RuleBinding."""
    __slots__ = ()


# -- Layer 6: Audit ----------------------------------------------------------

class TraceId(ScopedId):
    """ID for a TraceEntry (audit trail)."""
    __slots__ = ()


# -- Layer 11: Secrets -------------------------------------------------------

class SecretId(ScopedId):
    """ID for a Secret."""
    __slots__ = ()


# -- Layer 13: Connector -----------------------------------------------------

class ConnectorId(ScopedId):
    """ID for a Connector."""
    __slots__ = ()


# -- Layer 16: Scheduling ----------------------------------------------------

class ScheduleId(ScopedId):
    """ID for a RecurringSchedule."""
    __slots__ = ()


class JobId(ScopedId):
    """ID for a Job."""
    __slots__ = ()
