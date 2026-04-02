---
title: Django Integration
description: Integrate pyscoped with Django and Django REST Framework for automatic principal resolution, scoped object access, and audit management commands.
category: integrations
---

# Django Integration

pyscoped provides first-class Django support through middleware, a Django ORM backend,
Django REST Framework authentication and permission classes, and management commands
for auditing and compliance.

## Installation

```bash
pip install pyscoped[django]
```

This installs pyscoped along with Django-specific dependencies.

## Quick Start

### 1. Add to INSTALLED_APPS

```python
# settings.py
INSTALLED_APPS = [
    # ...
    "scoped.contrib.django",
    # ...
]
```

The `AppConfig.ready()` hook automatically initializes pyscoped when Django starts,
configuring the backend and client singletons so they are available throughout
the application lifecycle.

### 2. Add Middleware

```python
# settings.py
MIDDLEWARE = [
    # Place after AuthenticationMiddleware so request.user is available
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "scoped.contrib.django.middleware.ScopedContextMiddleware",
    # ...
]
```

`ScopedContextMiddleware` supports both synchronous and asynchronous views.
On Django 4.1+ it detects whether the current view is async and handles context
propagation accordingly, so you do not need separate middleware for async views.

### 3. Configure Settings

```python
# settings.py

# Required: which database alias to use for the DjangoORMBackend.
# Defaults to "default" if not set.
SCOPED_BACKEND_USING = "default"

# Required: your pyscoped API key for remote operations.
SCOPED_API_KEY = "sk-scoped-..."

# Optional: a callable path that receives the request and returns a principal ID.
# If set, this takes priority over the header-based resolution.
SCOPED_PRINCIPAL_RESOLVER = "myapp.auth.resolve_principal"

# Optional: the META key used to read the principal ID from the request header.
# Defaults to "HTTP_X_SCOPED_PRINCIPAL_ID".
SCOPED_PRINCIPAL_HEADER = "HTTP_X_SCOPED_PRINCIPAL_ID"

# Optional: a list of URL path prefixes exempt from principal resolution.
# Requests matching these paths will not have a ScopedContext set.
SCOPED_EXEMPT_PATHS = ["/health/", "/static/"]
```

### Principal Resolution Order

The middleware resolves the current principal using the following priority:

1. **Custom resolver** -- If `SCOPED_PRINCIPAL_RESOLVER` is configured, the
   callable is invoked with the request. If it returns a non-`None` value, that
   value is used as the principal ID.
2. **Header** -- The middleware reads `request.META[SCOPED_PRINCIPAL_HEADER]`.
   If the header is present and non-empty, its value is used.
3. **Fallback** -- If neither resolver nor header yields a principal, the
   middleware skips context setup and the request proceeds without a
   `ScopedContext`.

## DjangoORMBackend

The Django contrib package ships with `DjangoORMBackend`, which uses Django's
database connection (via the alias specified in `SCOPED_BACKEND_USING`) to
store principals, objects, scopes, and audit records. Migrations are included
in the `scoped.contrib.django` app and run with `manage.py migrate`.

```python
from scoped.contrib.django import get_backend, get_client

# These return singletons initialized during AppConfig.ready().
backend = get_backend()
client = get_client()
```

## Using scoped.objects in Views

After the middleware sets up the context, you can access scoped objects directly
in any view:

```python
import scoped

def document_list(request):
    # scoped.objects is automatically filtered to the current principal's
    # visible objects within the active context.
    documents = scoped.objects.filter(kind="document")
    return JsonResponse({"documents": [d.to_dict() for d in documents]})


def document_detail(request, object_id):
    doc = scoped.objects.get(object_id)
    return JsonResponse(doc.to_dict())
```

## Django REST Framework Integration

pyscoped ships with authentication, permission classes, and a user wrapper
designed for DRF.

### Settings

```python
# settings.py
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "scoped.contrib.django.rest_framework.ScopedAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "scoped.contrib.django.rest_framework.IsScopedPrincipal",
    ],
}
```

### ScopedAuthentication

`ScopedAuthentication` resolves the principal using the same order as the
middleware: custom resolver, header, then `request.user`. If a principal is
found, it returns a `ScopedUser` wrapper as `request.user` within DRF views.

```python
from scoped.contrib.django.rest_framework import ScopedUser

# ScopedUser exposes:
#   .principal_id  -- the resolved principal ID
#   .principal     -- the full principal object from the backend
#   .is_authenticated  -- always True
```

### IsScopedPrincipal

A permission class that grants access only when the request has a resolved
scoped principal. Use it as a baseline permission for any endpoint that
requires an identified principal.

### HasScopeAccess

A permission class that checks whether the current principal is a member of
the scope identified by a URL keyword argument. By default it reads the
`scope_id` kwarg, but you can customize this with `scoped_scope_id_kwarg`
on the view.

```python
from rest_framework.viewsets import ModelViewSet
from scoped.contrib.django.rest_framework import (
    ScopedAuthentication,
    HasScopeAccess,
)


class ProjectViewSet(ModelViewSet):
    """
    A viewset where access is gated by scope membership.
    The URL must include a `project_id` kwarg that maps to a scope.
    """

    authentication_classes = [ScopedAuthentication]
    permission_classes = [HasScopeAccess]
    scoped_scope_id_kwarg = "project_id"

    def get_queryset(self):
        # Only return objects visible within the current scope.
        return scoped.objects.filter(
            scope_id=self.kwargs["project_id"],
            kind="task",
        )
```

Wire the viewset into your URL configuration:

```python
# urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from myapp.views import ProjectViewSet

router = DefaultRouter()
router.register(r"projects/(?P<project_id>[^/.]+)/tasks", ProjectViewSet)

urlpatterns = [
    path("api/", include(router.urls)),
]
```

## ScopedModel — Django ORM Integration

`ScopedModel` is an abstract Django model base class that automatically syncs with
pyscoped's object layer. Every `save()` and `delete()` creates/updates/tombstones the
corresponding `ScopedObject` with full versioning and audit trail.

### Define a Model

```python
from django.db import models
from scoped.contrib.django.models import ScopedModel

class Invoice(ScopedModel):
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=20, default="draft")

    class ScopedMeta:
        object_type = "invoice"               # Required
        scoped_fields = ["amount", "currency", "status"]  # None = all fields
```

The model inherits two extra fields:
- `scoped_object_id` — links to the pyscoped `ScopedObject`
- `scoped_owner_id` — the owning principal's ID

### How save() works

```python
with scoped.as_principal(alice):
    inv = Invoice(amount=500, currency="USD")
    inv.save()
    # 1. Django persists the row (INSERT)
    # 2. pyscoped creates a ScopedObject + ObjectVersion
    # 3. scoped_object_id and scoped_owner_id are populated

    inv.amount = 600
    inv.save()
    # 1. Django updates the row
    # 2. pyscoped creates a new ObjectVersion (version 2)
```

Both operations run inside `transaction.atomic()` — if the pyscoped write fails,
the Django write rolls back too.

### How delete() works

```python
inv.delete()
# 1. pyscoped tombstones the ScopedObject
# 2. Django deletes the row
```

### Querying with visibility

```python
# Standard Django manager (no scoped filtering)
Invoice.objects.all()

# Scoped manager — filters to objects visible to the principal
Invoice.scoped_objects.for_principal(alice.id)
```

`for_principal()` queries pyscoped's visibility engine (owner + scope projections).
Falls back to `scoped_owner_id` filtering when no pyscoped client is configured.

### Field Serialization

`_to_scoped_dict()` auto-serializes Django field types:

| Field Type | Serialized As |
|---|---|
| `DecimalField` | `str` (e.g. `"99.95"`) |
| `DateTimeField` | ISO 8601 string |
| `UUIDField` | `str` |
| `ForeignKey` | `str(pk)` |
| All others | Value as-is |

Fields excluded: `pk`, `id`, `scoped_object_id`, `scoped_owner_id`.

### Context Helper for Non-HTTP Code

```python
from scoped.contrib.django.models import scoped_context_for

# In management commands, Celery tasks, etc.
with scoped_context_for("user-123"):
    Invoice(amount=100, currency="USD").save()
```

---

## Management Commands

The Django contrib app registers three management commands.

### scoped_audit

Prints a filterable audit log.

```bash
# Show the last 50 audit events
python manage.py scoped_audit --limit 50

# Filter by principal
python manage.py scoped_audit --principal "usr_abc123"

# Filter by action and date range
python manage.py scoped_audit --action "object.create" --since 2026-01-01
```

### scoped_compliance

Generates a compliance report covering principal permissions, scope
memberships, and object ownership.

```bash
python manage.py scoped_compliance --format json --output report.json
python manage.py scoped_compliance --format table
```

### scoped_health

Runs a health check against the configured backend and prints diagnostic
information.

```bash
python manage.py scoped_health
# Backend: DjangoORMBackend (using="default")
# Status: healthy
# Principals: 142
# Objects: 8,491
# Scopes: 37
```

## Full Example

```python
# settings.py
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rest_framework",
    "scoped.contrib.django",
    "myapp",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "scoped.contrib.django.middleware.ScopedContextMiddleware",
]

SCOPED_BACKEND_USING = "default"
SCOPED_API_KEY = "sk-scoped-production-key"
SCOPED_EXEMPT_PATHS = ["/health/", "/static/", "/favicon.ico"]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "scoped.contrib.django.rest_framework.ScopedAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "scoped.contrib.django.rest_framework.IsScopedPrincipal",
    ],
}
```

```python
# myapp/auth.py
def resolve_principal(request):
    """Custom resolver that maps Django users to scoped principals."""
    if request.user.is_authenticated:
        return f"usr_{request.user.pk}"
    return None
```

```python
# myapp/views.py
import scoped
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def my_objects(request):
    objects = scoped.objects.filter(kind="document")
    return Response([obj.to_dict() for obj in objects])
```

## Notes

- The `DjangoORMBackend` participates in Django's database transaction
  management. Wrap view logic in `transaction.atomic()` if you need
  transactional guarantees across multiple scoped operations.
- `get_client()` and `get_backend()` return the same singleton instances for
  the lifetime of the process. They are safe to call from anywhere after
  `AppConfig.ready()` has run.
- When running tests, use Django's standard `override_settings` to swap
  `SCOPED_BACKEND_USING` to a test database or use an in-memory backend.
