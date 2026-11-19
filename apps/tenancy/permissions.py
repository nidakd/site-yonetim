"""Rol → yetki eşlemesi. Görünümde bir düğmeyi gizlemek yetki değildir; her görünüm ve servis
burada tanımlı yetkiyi `require_perm` ile kontrol eder."""

from __future__ import annotations

from functools import wraps

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse

from .models import Role

PERMISSIONS: dict[str, set[str]] = {
    "org.view": {Role.ORG_ADMIN, Role.SITE_ADMIN, Role.ACCOUNTANT, Role.AUDITOR, Role.FIELD_STAFF},
    "site.create": {Role.ORG_ADMIN},
    "site.update": {Role.ORG_ADMIN, Role.SITE_ADMIN},
    "audit.view": {Role.ORG_ADMIN, Role.AUDITOR},
    "members.manage": {Role.ORG_ADMIN},
    # Faz 1: site işleri (siteye özel rol yalnızca o sitede geçerli)
    "site.view": {Role.ORG_ADMIN, Role.SITE_ADMIN, Role.ACCOUNTANT, Role.AUDITOR, Role.FIELD_STAFF},
    "property.manage": {Role.ORG_ADMIN, Role.SITE_ADMIN},
    "people.view": {Role.ORG_ADMIN, Role.SITE_ADMIN, Role.ACCOUNTANT, Role.AUDITOR, Role.FIELD_STAFF},
    "people.manage": {Role.ORG_ADMIN, Role.SITE_ADMIN},
    "finance.view": {Role.ORG_ADMIN, Role.SITE_ADMIN, Role.ACCOUNTANT, Role.AUDITOR},
    "billing.manage": {Role.ORG_ADMIN, Role.SITE_ADMIN, Role.ACCOUNTANT},
    "payments.manage": {Role.ORG_ADMIN, Role.SITE_ADMIN, Role.ACCOUNTANT},
    "expenses.manage": {Role.ORG_ADMIN, Role.SITE_ADMIN, Role.ACCOUNTANT},
    "ledger.lock": {Role.ORG_ADMIN, Role.ACCOUNTANT},
}


# Herhangi bir üyelik yeterli olan yetkiler (siteye özel üye de kuruluş panelini açabilir;
# hangi siteleri göreceğini görünüm ayrıca sınırlar).
ANY_MEMBERSHIP = {"org.view"}


def roles_for(request, site_id: int | None = None, *, include_site_roles: bool = False) -> set[str]:
    """Kullanıcının aktif kuruluşta, verilen site için geçerli rolleri (kuruluş geneli + siteye özel)."""
    roles = set()
    for m in getattr(request, "memberships", []):
        if m.site_id is None or include_site_roles or (site_id is not None and m.site_id == site_id):
            roles.add(m.role)
    return roles


def has_perm(request, perm: str, site_id: int | None = None) -> bool:
    if perm not in PERMISSIONS:
        raise KeyError(f"Tanımsız yetki: {perm}")
    roles = roles_for(request, site_id, include_site_roles=perm in ANY_MEMBERSHIP)
    return bool(roles & PERMISSIONS[perm])


def panel_view(perm: str = "org.view"):
    """Panel görünümleri için: giriş + (gerekiyorsa) 2FA + aktif kuruluş + yetki."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(f"{reverse('accounts:login')}?next={request.path}")
            if settings.REQUIRE_2FA and not request.user.is_verified():
                return redirect("accounts:two_factor")
            if getattr(request, "organization", None) is None:
                return redirect("tenancy:no_organization")
            site_id = kwargs.get("site_id")
            if not has_perm(request, perm, site_id):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def site_view(perm: str = "site.view"):
    """Siteye bağlı panel görünümleri: `site_id` URL parametresini yetki kontrolünden sonra
    `site` nesnesine çevirir. Başka kuruluşun sitesi bağlam dışında kaldığı için 404 olur."""

    def decorator(view):
        @panel_view(perm)
        @wraps(view)
        def wrapper(request, site_id: int, *args, **kwargs):
            from django.shortcuts import get_object_or_404

            from .models import Site

            site = get_object_or_404(Site, pk=site_id)
            request.session["active_site_id"] = site.pk
            return view(request, site, *args, **kwargs)

        return wrapper

    return decorator


def require(request, perm: str, site_id: int | None = None) -> None:
    """Görünüm içinde ek yetki kontrolü (ör. aynı sayfada okuma serbest, yazma yetkili)."""
    if not has_perm(request, perm, site_id):
        raise PermissionDenied
