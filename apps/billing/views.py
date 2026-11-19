from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.audit.services import record
from apps.core.forms import CancelForm, EndDateForm
from apps.core.utils import changes, snapshot
from apps.property.models import Unit
from apps.tenancy.permissions import has_perm, site_view

from . import services
from .forms import AgreementForm, BatchForm, ChargeTypeForm
from .models import ChargeBatch, ChargeType, DuesAgreement


@site_view("finance.view")
def batch_list(request, site):
    services.setup_site(site)
    batches = ChargeBatch.objects.filter(site=site).select_related("charge_type", "block", "unit__block")
    page = Paginator(batches, 30).get_page(request.GET.get("sayfa"))
    return render(request, "billing/batch_list.html", {"site": site, "page": page})


@site_view("billing.manage")
def batch_create(request, site):
    services.setup_site(site)
    today = timezone.localdate()
    initial = {"period": today.replace(day=1), "due_date": today.replace(day=min(10, 28)), "method": "per_unit"}
    if request.GET.get("bolum"):
        initial.update(scope="unit", unit=request.GET["bolum"])
    form = BatchForm(request.POST or None, site=site, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            batch = form.save(commit=False)
            batch.created_by = request.user
            batch.save()
            record("batch.drafted", batch, {"donem": str(batch.period), "yontem": batch.method}, site_id=site.pk)
        return redirect("billing:batch_detail", site_id=site.pk, batch_id=batch.pk)
    return render(request, "billing/batch_form.html", {"site": site, "form": form})


@site_view("finance.view")
def batch_detail(request, site, batch_id: int):
    batch = get_object_or_404(
        ChargeBatch.objects.select_related("charge_type", "block", "unit__block", "approved_by", "cancelled_by"),
        pk=batch_id,
        site=site,
    )
    ctx = {
        "site": site,
        "batch": batch,
        "can_manage": has_perm(request, "billing.manage", site.pk),
        "cancel_form": CancelForm(),
    }
    if batch.status == ChargeBatch.Status.DRAFT:
        ctx["preview"] = services.compute_preview(batch)
    else:
        ctx["charges"] = batch.charges.select_related("unit__block", "agreement").order_by(
            "unit__block__order", "unit__block__name", "unit__sort_key"
        )
    return render(request, "billing/batch_detail.html", ctx)


@site_view("billing.manage")
def batch_approve(request, site, batch_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    batch = get_object_or_404(ChargeBatch, pk=batch_id, site=site)
    try:
        batch = services.approve(batch, request.user)
    except services.BillingError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Tahakkuk onaylandı: {batch.unit_count} bölüm borçlandırıldı.")
    return redirect("billing:batch_detail", site_id=site.pk, batch_id=batch.pk)


@site_view("billing.manage")
def batch_discard(request, site, batch_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    batch = get_object_or_404(ChargeBatch, pk=batch_id, site=site, status=ChargeBatch.Status.DRAFT)
    with transaction.atomic():
        record("batch.discarded", batch, {"donem": str(batch.period)}, site_id=site.pk)
        batch.delete()
    messages.success(request, "Taslak silindi.")
    return redirect("billing:batch_list", site_id=site.pk)


@site_view("billing.manage")
def batch_cancel(request, site, batch_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    batch = get_object_or_404(ChargeBatch, pk=batch_id, site=site)
    form = CancelForm(request.POST)
    if not form.is_valid():
        messages.error(request, "İptal nedeni yazın.")
    else:
        try:
            services.cancel(batch, request.user, form.cleaned_data["reason"])
        except services.BillingError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Tahakkuk iptal edildi; ters kayıt atıldı.")
    return redirect("billing:batch_detail", site_id=site.pk, batch_id=batch.pk)


@site_view("billing.manage")
def agreement_create(request, site, unit_id: int):
    unit = get_object_or_404(Unit, pk=unit_id, site=site)
    form = AgreementForm(request.POST or None, unit=unit, initial={"valid_from": timezone.localdate().replace(day=1)})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            agreement = form.save()
            record(
                "agreement.created",
                agreement,
                {
                    "tur": agreement.kind,
                    "tutar": agreement.amount,
                    "oran": str(agreement.discount_percent) if agreement.discount_percent else None,
                    "baslangic": str(agreement.valid_from),
                    "karar": agreement.decision_ref,
                },
                site_id=site.pk,
            )
        messages.success(request, "Özel aidat anlaşması eklendi. Bundan sonraki aidat tahakkuklarında uygulanır.")
        return redirect("property:unit_detail", site_id=site.pk, unit_id=unit.pk)
    return render(request, "billing/agreement_form.html", {"site": site, "unit": unit, "form": form})


@site_view("billing.manage")
def agreement_end(request, site, agreement_id: int):
    agreement = get_object_or_404(DuesAgreement.objects.select_related("unit"), pk=agreement_id, unit__site=site)
    if request.method == "POST":
        form = EndDateForm(request.POST, start=agreement.valid_from)
        if form.is_valid():
            services.end_agreement(agreement, form.cleaned_data["end_date"])
            messages.success(request, "Anlaşma sonlandırıldı.")
        else:
            messages.error(request, "; ".join(e for errs in form.errors.values() for e in errs))
    return redirect("property:unit_detail", site_id=site.pk, unit_id=agreement.unit_id)


@site_view("billing.manage")
def charge_type_form(request, site, type_id: int | None = None):
    obj = get_object_or_404(ChargeType, pk=type_id, site=site) if type_id else None
    fields = ChargeTypeForm.Meta.fields
    before = snapshot(obj, fields) if obj else None
    form = ChargeTypeForm(request.POST or None, instance=obj, site=site)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            ct = form.save()
            if before:
                record("charge_type.updated", ct, changes(before, snapshot(ct, fields)), site_id=site.pk)
            else:
                record("charge_type.created", ct, {"ad": ct.name}, site_id=site.pk)
        messages.success(request, "Borç türü kaydedildi.")
        return redirect("ledger:settings", site_id=site.pk)
    title = f"{obj.name} · düzenle" if obj else "Yeni borç türü"
    return render(
        request, "property/simple_form.html", {"site": site, "form": form, "title": title, "back": "ledger:settings"}
    )
