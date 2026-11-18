from apps.core import context

from .models import Membership

SESSION_KEY = "active_org_id"


class TenantMiddleware:
    """Oturumdaki aktif kuruluşu doğrular ve isteği o kuruluşun bağlamında çalıştırır.

    Kullanıcı aktif kuruluşa artık üye değilse (üyelik kaldırıldı) bağlam kurulmaz.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.organization = None
        request.memberships = []
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return self.get_response(request)

        all_memberships = list(
            Membership.objects.filter(user=user, is_active=True, organization__is_active=True)
            .select_related("organization")
            .order_by("organization__name")
        )
        request.available_organizations = list({m.organization_id: m.organization for m in all_memberships}.values())
        org_id = request.session.get(SESSION_KEY)
        if org_id not in {o.pk for o in request.available_organizations}:
            org_id = request.available_organizations[0].pk if request.available_organizations else None
            if org_id:
                request.session[SESSION_KEY] = org_id
        if org_id is None:
            return self.get_response(request)

        request.memberships = [m for m in all_memberships if m.organization_id == org_id]
        request.organization = request.memberships[0].organization
        with context.tenant_context(org_id):
            return self.get_response(request)
