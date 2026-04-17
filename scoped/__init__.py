"""Scoped: Universal object-isolation and tenancy-scoping framework.

Quick start::

    import scoped

    scoped.init()  # Zero-config in-memory SQLite
    # or: scoped.init(database_url="postgresql://user:pass@host/db")

    alice = scoped.principals.create("Alice")

    with scoped.as_principal(alice):
        doc, v1 = scoped.objects.create("invoice", data={"amount": 500})
        team = scoped.scopes.create("Engineering")
        scoped.scopes.project(doc, team)
        trail = scoped.audit.for_object(doc.id)

See ``scoped.client`` for full documentation.
"""

from __future__ import annotations

import sys
from types import ModuleType as _ModuleType

__version__ = "1.3.0"

from scoped.client import ScopedClient, init  # noqa: F401


_NAMESPACE_NAMES = frozenset({
    "principals", "objects", "scopes", "audit", "secrets", "environments",
})


class _ScopedModule(_ModuleType):
    """Module subclass that proxies namespace access through the default client.

    Python's import machinery sets ``scoped.<submodule>`` as a real attribute
    whenever something imports, say, ``scoped.objects.manager``.  That would
    shadow a plain ``__getattr__`` on the package, so after the first internal
    import the documented ``scoped.objects.create(...)`` form would resolve to
    the submodule instead of the namespace.  By overriding ``__getattribute__``
    at the package level we always route the documented names through the
    default client regardless of what import state has materialized.
    """

    def __getattribute__(self, name: str):
        if name in _NAMESPACE_NAMES:
            from scoped.client import _get_default_client

            return getattr(_get_default_client(), name)
        if name == "as_principal":
            from scoped.client import _get_default_client

            return _get_default_client().as_principal
        if name == "services":
            from scoped.client import _get_default_client

            return _get_default_client().services
        if name == "register_type":
            from scoped._type_registry import _registry

            return _registry.register
        return super().__getattribute__(name)


sys.modules[__name__].__class__ = _ScopedModule
