from .permissions import has_perm


def tenant(request):
    org = getattr(request, "organization", None)
    if org is None:
        return {}
    return {
        "current_org": org,
        "available_organizations": getattr(request, "available_organizations", []),
        # Şablonlarda yalnızca menü/düğme göstermek için; asıl kontrol görünümlerde (panel_view).
        "can": {
            "create_site": has_perm(request, "site.create"),
            "view_audit": has_perm(request, "audit.view"),
            "manage_members": has_perm(request, "members.manage"),
        },
    }
