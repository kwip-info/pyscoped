from django.core.exceptions import PermissionDenied

from pyscoped import ScopeContext

from .models import Membership


def resolve_context(request):
    # Run after the application's authentication middleware. Never accept actor headers.
    user = request.user
    if not user.is_authenticated:
        return None
    organization_id = request.session.get("organization_id")
    if (
        not organization_id
        or not Membership.objects.filter(
            user=user,
            organization_id=organization_id,
        ).exists()
    ):
        raise PermissionDenied("No membership in the selected organization.")
    return ScopeContext(actor=user, scope=organization_id)
