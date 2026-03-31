"""Minimal URL config for Django adapter tests."""

from django.http import JsonResponse

urlpatterns = []


def index(request):
    from scoped.identity.context import ScopedContext

    ctx = ScopedContext.current_or_none()
    if ctx:
        return JsonResponse({"principal_id": ctx.principal_id})
    return JsonResponse({"principal_id": None})
