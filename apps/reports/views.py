from datetime import date

from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from apps.property.models import Unit
from apps.tenancy.permissions import site_view

from . import services


@site_view("finance.view")
def debtors(request, site):
    rows = services.debtors(site)
    return render(
        request, "reports/debtors.html", {"site": site, "rows": rows, "total": sum(r["balance"] for r in rows)}
    )


@site_view("finance.view")
def unit_statement(request, site, unit_id: int):
    unit = get_object_or_404(Unit.objects.select_related("block"), pk=unit_id, site=site)
    start = None
    raw = request.GET.get("baslangic")
    if raw:
        try:
            start = date.fromisoformat(raw)
        except ValueError:
            start = None
    rows = services.unit_statement(unit, start=start)
    from apps.people.views import current_people

    return render(
        request,
        "reports/statement.html",
        {
            "site": site,
            "unit": unit,
            "rows": rows,
            "balance": rows[-1].balance if rows else 0,
            "start": start,
            "people": current_people(unit),
            "today": timezone.localdate(),
            "open_charges": services.unit_open_charges(unit),
        },
    )
