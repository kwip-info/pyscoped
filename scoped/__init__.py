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

__version__ = "0.4.1"

from scoped.client import ScopedClient, init  # noqa: F401


def __getattr__(name: str):
    """Proxy module-level access to the default client's namespaces.

    After calling ``scoped.init()``, these attributes are available:

    - ``scoped.principals`` — create and manage identities
    - ``scoped.objects`` — versioned, isolated data objects
    - ``scoped.scopes`` — tenancy, sharing, and access control
    - ``scoped.audit`` — query the tamper-evident audit trail
    - ``scoped.secrets`` — encrypted vault with zero-trust access
    - ``scoped.as_principal(p)`` — set the acting principal
    - ``scoped.services`` — raw ScopedServices escape hatch
    """
    _namespace_names = {
        "principals", "objects", "scopes", "audit", "secrets",
    }
    if name in _namespace_names:
        from scoped.client import _get_default_client

        return getattr(_get_default_client(), name)

    if name == "as_principal":
        from scoped.client import _get_default_client

        return _get_default_client().as_principal

    if name == "services":
        from scoped.client import _get_default_client

        return _get_default_client().services

    raise AttributeError(f"module 'scoped' has no attribute {name!r}")
