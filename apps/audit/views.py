from django.core.paginator import Paginator
from django.shortcuts import render

from apps.tenancy.permissions import panel_view

from .models import AuditEvent
from .services import ACTIONS


@panel_view("audit.view")
def audit_list(request):
    events = AuditEvent.objects.filter(organization=request.organization).select_related("actor")
    action = request.GET.get("islem")
    if action in ACTIONS:
        events = events.filter(action=action)
    page = Paginator(events, 50).get_page(request.GET.get("sayfa"))
    return render(request, "audit/list.html", {"page": page, "actions": ACTIONS, "selected": action})
