"""Namespace proxy classes for the simplified pyscoped SDK.

These classes provide a streamlined API surface over the internal
16-layer service architecture. They are accessed via ``ScopedClient``
properties (e.g. ``client.objects``, ``client.scopes``) or at the
module level after ``scoped.init()`` (e.g. ``scoped.objects.create(...)``).

Each namespace:
- Delegates to the underlying service layer without modifying it
- Infers the acting principal from ``ScopedContext`` when not passed explicitly
- Accepts model objects or string IDs interchangeably
- Converts string enums to their typed equivalents automatically
"""

from scoped._namespaces.audit import AuditNamespace
from scoped._namespaces.objects import ObjectsNamespace
from scoped._namespaces.principals import PrincipalsNamespace
from scoped._namespaces.scopes import ScopesNamespace
from scoped._namespaces.secrets import SecretsNamespace

__all__ = [
    "AuditNamespace",
    "ObjectsNamespace",
    "PrincipalsNamespace",
    "ScopesNamespace",
    "SecretsNamespace",
]
