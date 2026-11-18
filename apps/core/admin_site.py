"""Platform yönetim paneli (yalnızca platform personeli, iki adımlı doğrulamayla).

Kuruluş ve üyelik yönetimi içindir. Tüm görünümler platform bağlamında çalışır (RLS kapalı);
bu yüzden kiracı verisini gösteren model buraya kaydedilmez.
"""

from functools import update_wrapper

from django_otp.admin import OTPAdminSite

from .context import platform_context


class PlatformAdminSite(OTPAdminSite):
    site_header = "Site Yönetim · Platform"
    site_title = "Platform"
    index_title = "Platform yönetimi"

    def admin_view(self, view, cacheable=False):
        inner = super().admin_view(view, cacheable)

        def wrapped(request, *args, **kwargs):
            with platform_context():
                return inner(request, *args, **kwargs)

        return update_wrapper(wrapped, inner)
