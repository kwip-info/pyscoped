"""Django REST Framework integration for pyscoped.

Provides authentication and permission classes that work with DRF
viewsets and API views, bridging DRF's auth system with pyscoped's
principal-based identity model.

Usage::

    # settings.py
    REST_FRAMEWORK = {
        "DEFAULT_AUTHENTICATION_CLASSES": [
            "scoped.contrib.django.rest_framework.ScopedAuthentication",
        ],
        "DEFAULT_PERMISSION_CLASSES": [
            "scoped.contrib.django.rest_framework.IsScopedPrincipal",
        ],
    }

    # views.py
    from rest_framework.viewsets import ViewSet
    from scoped.contrib.django.rest_framework import HasScopeAccess

    class InvoiceViewSet(ViewSet):
        permission_classes = [HasScopeAccess]
        scoped_scope_id_kwarg = "scope_id"  # URL kwarg
        ...

Requires ``djangorestframework`` — install separately.
"""

from __future__ import annotations

from typing import Any

try:
    from rest_framework.authentication import BaseAuthentication
    from rest_framework.exceptions import AuthenticationFailed
    from rest_framework.permissions import BasePermission
    from rest_framework.request import Request
except ImportError as exc:
    raise ImportError(
        "scoped.contrib.django.rest_framework requires djangorestframework. "
        "Install with: pip install djangorestframework"
    ) from exc


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class ScopedAuthentication(BaseAuthentication):
    """Authenticate requests by resolving a pyscoped Principal.

    Resolution order:
    1. ``SCOPED_PRINCIPAL_RESOLVER`` setting (callable)
    2. ``SCOPED_PRINCIPAL_HEADER`` header (default ``HTTP_X_SCOPED_PRINCIPAL_ID``)
    3. ``request.user.id`` if Django auth is active

    On success, sets ``request.user`` to a lightweight wrapper and
    ``request.auth`` to the ``Principal`` object.
    """

    def authenticate(self, request: Request) -> tuple[Any, Any] | None:
        from scoped.contrib.django import get_client

        principal = self._resolve(request)
        if principal is None:
            return None

        user = ScopedUser(principal)
        return (user, principal)

    def _resolve(self, request: Request):
        from django.conf import settings

        # 1. Custom resolver
        resolver_path = getattr(settings, "SCOPED_PRINCIPAL_RESOLVER", None)
        if resolver_path:
            if isinstance(resolver_path, str):
                from django.utils.module_loading import import_string

                resolver = import_string(resolver_path)
            else:
                resolver = resolver_path
            result = resolver(request)
            if result is not None:
                return result

        # 2. Header-based
        header = getattr(
            settings, "SCOPED_PRINCIPAL_HEADER", "HTTP_X_SCOPED_PRINCIPAL_ID"
        )
        principal_id = request.META.get(header)
        if principal_id:
            from scoped.contrib.django import get_client

            p = get_client().principals.find(principal_id)
            if p is not None:
                return p

        # 3. Django auth user
        django_user = getattr(request, "_request", request)
        django_user = getattr(django_user, "user", None)
        if django_user and getattr(django_user, "is_authenticated", False):
            from scoped.contrib.django import get_client

            return get_client().principals.find(str(django_user.id))

        return None


class ScopedUser:
    """Lightweight user wrapper around a pyscoped Principal.

    Satisfies DRF's expectation that ``request.user`` has
    ``is_authenticated`` and an identity.
    """

    def __init__(self, principal: Any) -> None:
        self.principal = principal
        self.id = principal.id
        self.pk = principal.id
        self.is_authenticated = True
        self.is_active = principal.is_active

    def __str__(self) -> str:
        return self.principal.display_name or self.principal.id


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class IsScopedPrincipal(BasePermission):
    """Allow access only if the request has a resolved pyscoped Principal."""

    def has_permission(self, request: Request, view: Any) -> bool:
        return (
            request.auth is not None
            and hasattr(request.auth, "id")
        )


class HasScopeAccess(BasePermission):
    """Check that the principal is a member of the scope specified in the URL.

    The view must define ``scoped_scope_id_kwarg`` (default ``"scope_id"``)
    pointing to the URL kwarg that contains the scope ID.
    """

    def has_permission(self, request: Request, view: Any) -> bool:
        if request.auth is None:
            return False

        kwarg_name = getattr(view, "scoped_scope_id_kwarg", "scope_id")
        scope_id = view.kwargs.get(kwarg_name)
        if not scope_id:
            return True  # No scope constraint on this endpoint

        from scoped.contrib.django import get_client

        client = get_client()
        services = client.services
        return services.scopes.is_member(scope_id, request.auth.id)
