from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect

from apps.audit.services import record
from apps.property.models import Unit
from apps.tenancy.permissions import site_view

from . import services


@site_view("payments.manage")
def send_statement(request, site, unit_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    unit = get_object_or_404(Unit.objects.select_related("block", "site"), pk=unit_id, site=site)
    with transaction.atomic():
        sent = services.send_statement(unit, user=request.user, request=request)
        if sent:
            record("notification.sent", unit, {"tur": "ekstre", "alici": len(sent)}, site_id=site.pk)
    ok = [n for n in sent if n.status == "sent"]
    if not sent:
        messages.warning(request, "E-postası kayıtlı ve bildirim alan bir malik/kiracı yok.")
    elif len(ok) == len(sent):
        messages.success(request, f"Hesap ekstresi {len(ok)} kişiye e-postayla gönderildi.")
    else:
        messages.error(request, f"{len(sent) - len(ok)} e-posta gönderilemedi; ayrıntı bölüm sayfasında.")
    return redirect("property:unit_detail", site_id=site.pk, unit_id=unit.pk)
