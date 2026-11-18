from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from apps.core.admin_site import PlatformAdminSite

admin.site.__class__ = PlatformAdminSite

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="tenancy:dashboard", permanent=False)),
    path("", include("apps.accounts.urls")),
    path("panel/", include("apps.tenancy.urls")),
    path("panel/islem-gecmisi/", include("apps.audit.urls")),
    path(settings.ADMIN_URL, admin.site.urls),
]

handler403 = "apps.core.views.forbidden"
handler404 = "apps.core.views.not_found"
