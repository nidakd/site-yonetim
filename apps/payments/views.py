from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from apps.billing.services import open_charges
from apps.core.forms import CancelForm
from apps.property.models import Unit
from apps.tenancy.permissions import has_perm, site_view

from . import services
from .forms import PaymentForm
from .models import Payment


@site_view("finance.view")
def payment_list(request, site):
    payments = Payment.objects.filter(site=site).select_related("unit__block", "account")
    if request.GET.get("bolum", "").isdigit():
        payments = payments.filter(unit_id=int(request.GET["bolum"]))
    page = Paginator(payments, 50).get_page(request.GET.get("sayfa"))
    return render(request, "payments/payment_list.html", {"site": site, "page": page})


def _unit_from(request, site):
    raw = request.POST.get("unit") or request.GET.get("bolum") or request.GET.get("unit")
    if raw and str(raw).isdigit():
        return Unit.objects.filter(pk=int(raw), site=site, is_archived=False).select_related("block").first()
    return None


@site_view("payments.manage")
def payment_create(request, site):
    from apps.billing.services import setup_site

    setup_site(site)
    unit = _unit_from(request, site)
    charges = open_charges(unit) if unit else []
    form = PaymentForm(
        request.POST or None, site=site, unit=unit, open_charges=charges, initial={"unit": unit.pk if unit else None}
    )
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            payment = services.create_payment(
                site,
                d["unit"],
                amount=d["amount"],
                day=d["date"],
                channel=d["channel"],
                account=d["account"],
                payer=d["payer"],
                reference=d["reference"],
                note=d["note"],
                preferred=[int(x) for x in d["preferred"]],
                user=request.user,
            )
        except services.PaymentError as exc:
            form.add_error(None, str(exc))
        else:
            from apps.comms.services import send_receipt

            sent = send_receipt(payment, user=request.user, request=request)
            mailed = sum(1 for n in sent if n.status == "sent")
            note = f" Makbuz {mailed} kişiye e-postayla gönderildi." if mailed else ""
            messages.success(request, f"Tahsilat kaydedildi. Makbuz no {payment.receipt_no}.{note}")
            return redirect("payments:payment_detail", site_id=site.pk, payment_id=payment.pk)
    ctx = {
        "site": site,
        "form": form,
        "unit": unit,
        "charges": charges,
        "advance": services.advance_balance(unit) if unit else 0,
        "debt": sum(c.remaining for c in charges),
    }
    if request.htmx and request.method == "GET":
        return render(request, "payments/_unit_panel.html", ctx)
    return render(request, "payments/payment_form.html", ctx)


@site_view("finance.view")
def payment_detail(request, site, payment_id: int):
    payment = get_object_or_404(
        Payment.objects.select_related("unit__block", "account", "payer", "created_by", "cancelled_by"),
        pk=payment_id,
        site=site,
    )
    allocations = payment.allocations.select_related("charge__charge_type").order_by("charge__due_date")
    return render(
        request,
        "payments/payment_detail.html",
        {
            "site": site,
            "payment": payment,
            "allocations": allocations,
            "cancel_form": CancelForm(),
            "unallocated": services.unallocated(payment) if payment.status == "valid" else 0,
            "can_manage": has_perm(request, "payments.manage", site.pk),
        },
    )


@site_view("finance.view")
def receipt(request, site, payment_id: int):
    payment = get_object_or_404(
        Payment.objects.select_related("unit__block", "account").exclude(channel=Payment.Channel.OPENING),
        pk=payment_id,
        site=site,
    )
    allocations = payment.allocations.filter(from_advance=False).select_related("charge").order_by("charge__due_date")
    return render(
        request,
        "payments/receipt.html",
        {
            "site": site,
            "payment": payment,
            "allocations": allocations,
            "advance": payment.amount - sum(a.amount for a in allocations),
        },
    )


@site_view("payments.manage")
def payment_cancel(request, site, payment_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    payment = get_object_or_404(Payment, pk=payment_id, site=site)
    form = CancelForm(request.POST)
    if not form.is_valid():
        messages.error(request, "İptal nedeni yazın.")
    else:
        try:
            services.cancel_payment(payment, request.user, form.cleaned_data["reason"])
        except services.PaymentError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Tahsilat iptal edildi; borçlar yeniden açıldı.")
    return redirect("payments:payment_detail", site_id=site.pk, payment_id=payment.pk)
