from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from apps.audit.services import record

from .forms import SiteForm
from .middleware import SESSION_KEY
from .models import Site
from .permissions import has_perm, panel_view


@panel_view()
def dashboard(request):
    sites = Site.objects.all()
    if not has_perm(request, "site.create"):  # siteye özel rolü olan yalnızca kendi sitelerini görür
        site_ids = {m.site_id for m in request.memberships if m.site_id}
        if not any(m.site_id is None for m in request.memberships):
            sites = sites.filter(pk__in=site_ids)
    return render(request, "tenancy/dashboard.html", {"sites": sites})


@panel_view("site.create")
def site_create(request):
    form = SiteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            site = form.save()
            record("site.created", site, {"name": site.name}, site_id=site.pk)
        messages.success(request, f"{site.name} oluşturuldu.")
        return redirect("tenancy:site_detail", site_id=site.pk)
    return render(request, "tenancy/site_form.html", {"form": form, "title": "Yeni site"})


@panel_view()
def site_detail(request, site_id: int):
    site = get_object_or_404(Site, pk=site_id)  # başka kuruluşun sitesi: bağlam dışı → 404
    if not any(m.site_id in (None, site.pk) for m in request.memberships):
        raise Http404
    request.session["active_site_id"] = site.pk
    return render(
        request, "tenancy/site_detail.html", {"site": site, "can_edit": has_perm(request, "site.update", site.pk)}
    )


@panel_view("site.update")
def site_update(request, site_id: int):
    site = get_object_or_404(Site, pk=site_id)
    before = {f: getattr(site, f) for f in SiteForm.Meta.fields}
    form = SiteForm(request.POST or None, instance=site)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            site = form.save()
            changed = {
                f: {"eski": before[f], "yeni": getattr(site, f)} for f in before if before[f] != getattr(site, f)
            }
            record("site.updated", site, changed, site_id=site.pk)
        messages.success(request, "Site bilgileri güncellendi.")
        return redirect("tenancy:site_detail", site_id=site.pk)
    return render(request, "tenancy/site_form.html", {"form": form, "title": f"{site.name} · düzenle"})


def switch_organization(request, org_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    if org_id not in {o.pk for o in getattr(request, "available_organizations", [])}:
        raise Http404
    request.session[SESSION_KEY] = org_id
    request.session.pop("active_site_id", None)
    from apps.core.context import tenant_context

    with tenant_context(org_id):
        record("org.switched", organization_id=org_id)
    return redirect("tenancy:dashboard")


def no_organization(request):
    if not request.user.is_authenticated:
        return redirect("accounts:login")
    if getattr(request, "organization", None) is not None:
        return redirect("tenancy:dashboard")
    return render(request, "tenancy/no_organization.html")
