from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render

from apps.audit.services import record
from apps.core.utils import changes, snapshot
from apps.tenancy.permissions import site_view

from .forms import PortalSettingsForm
from .models import PortalSettings

FIELDS = PortalSettingsForm.Meta.fields


@site_view("site.update")
def portal_settings(request, site):
    obj = PortalSettings.objects.filter(site=site).first() or PortalSettings(site=site)
    before = snapshot(obj, FIELDS)
    form = PortalSettingsForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            obj = form.save()
            record("portal.updated", obj, changes(before, snapshot(obj, FIELDS)), site_id=site.pk)
        messages.success(request, "Sakin portalı ayarları kaydedildi.")
        return redirect("residents_panel:portal_settings", site_id=site.pk)
    return render(request, "residents/portal_settings.html", {"site": site, "form": form})
