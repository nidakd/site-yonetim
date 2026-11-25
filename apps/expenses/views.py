from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.audit.services import record
from apps.core.forms import CancelForm
from apps.ledger.services import account_balances, cash_accounts
from apps.tenancy.permissions import has_perm, site_view

from . import services
from .forms import AccountForm, BillForm, BillPaymentForm, ExpenseForm, TransferForm, VendorForm
from .models import Bill, BillPayment, Expense, Transfer


@site_view("finance.view")
def expense_list(request, site):
    from apps.billing.services import setup_site

    setup_site(site)
    can_manage = has_perm(request, "expenses.manage", site.pk)
    form, vendor_form = ExpenseForm(site=site), VendorForm(site=site, prefix="ted")
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied
        if "ted-name" in request.POST:
            vendor_form = VendorForm(request.POST, site=site, prefix="ted")
            if vendor_form.is_valid():
                with transaction.atomic():
                    vendor = vendor_form.save()
                    record("vendor.created", vendor, {"ad": vendor.name}, site_id=site.pk)
                messages.success(request, f"{vendor.name} eklendi.")
                return redirect("expenses:expense_list", site_id=site.pk)
        else:
            form = ExpenseForm(request.POST, site=site)
            if form.is_valid():
                d = form.cleaned_data
                try:
                    services.create_expense(
                        site,
                        day=d["date"],
                        category=d["category"],
                        paid_from=d["paid_from"],
                        amount=d["amount"],
                        description=d["description"],
                        vendor=d["vendor"],
                        document_number=d["document_number"],
                        user=request.user,
                    )
                except services.ExpenseError as exc:
                    form.add_error(None, str(exc))
                else:
                    messages.success(request, "Gider kaydedildi.")
                    return redirect("expenses:expense_list", site_id=site.pk)
    expenses = Expense.objects.filter(site=site).select_related("category", "vendor", "paid_from")
    page = Paginator(expenses, 50).get_page(request.GET.get("sayfa"))
    return render(
        request,
        "expenses/expense_list.html",
        {
            "site": site,
            "page": page,
            "form": form,
            "vendor_form": vendor_form,
            "can_manage": can_manage,
            "cancel_form": CancelForm(),
        },
    )


@site_view("expenses.manage")
def expense_cancel(request, site, expense_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    expense = get_object_or_404(Expense, pk=expense_id, site=site)
    form = CancelForm(request.POST)
    if form.is_valid():
        try:
            services.cancel_expense(expense, request.user, form.cleaned_data["reason"])
            messages.success(request, "Gider iptal edildi; ters kayıt atıldı.")
        except services.ExpenseError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "İptal nedeni yazın.")
    return redirect("expenses:expense_list", site_id=site.pk)


@site_view("finance.view")
def cash_bank(request, site):
    from apps.billing.services import setup_site

    setup_site(site)
    can_manage = has_perm(request, "expenses.manage", site.pk)
    transfer_form, account_form = TransferForm(site=site, prefix="vir"), AccountForm(prefix="hes")
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied
        if "vir-amount" in request.POST:
            transfer_form = TransferForm(request.POST, site=site, prefix="vir")
            if transfer_form.is_valid():
                d = transfer_form.cleaned_data
                try:
                    services.create_transfer(
                        site,
                        day=d["date"],
                        from_account=d["from_account"],
                        to_account=d["to_account"],
                        amount=d["amount"],
                        description=d["description"],
                        user=request.user,
                    )
                except services.ExpenseError as exc:
                    transfer_form.add_error(None, str(exc))
                else:
                    messages.success(request, "Virman kaydedildi.")
                    return redirect("expenses:cash_bank", site_id=site.pk)
        else:
            account_form = AccountForm(request.POST, prefix="hes")
            if account_form.is_valid():
                d = account_form.cleaned_data
                try:
                    account = services.create_account(site, name=d["name"], group=d["group"], iban=d["iban"])
                except services.ExpenseError as exc:
                    account_form.add_error(None, str(exc))
                else:
                    messages.success(request, f"{account} eklendi.")
                    return redirect("expenses:cash_bank", site_id=site.pk)
    balances = account_balances(site)
    accounts = [(a, balances.get(a.pk, 0)) for a in cash_accounts(site, active_only=False)]
    return render(
        request,
        "expenses/cash_bank.html",
        {
            "site": site,
            "accounts": accounts,
            "total": sum(b for _, b in accounts),
            "transfers": Transfer.objects.filter(site=site).select_related("from_account", "to_account")[:20],
            "transfer_form": transfer_form,
            "account_form": account_form,
            "can_manage": can_manage,
        },
    )


@site_view("finance.view")
def bill_list(request, site):
    from apps.billing.services import setup_site

    setup_site(site)
    can_manage = has_perm(request, "expenses.manage", site.pk)
    form = BillForm(site=site)
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied
        form = BillForm(request.POST, site=site)
        if form.is_valid():
            d = form.cleaned_data
            try:
                bill = services.create_bill(site, user=request.user, day=d.pop("date"), **d)
            except services.ExpenseError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Fatura kaydedildi. Ödendiğinde \u201cÖde\u201d ile kapatın.")
                return redirect("expenses:bill_detail", site_id=site.pk, bill_id=bill.pk)
    show = request.GET.get("goster", "acik")
    bills = (
        services.open_bills(site)
        if show == "acik"
        else (Bill.objects.filter(site=site).select_related("category", "vendor"))
    )
    page = Paginator(bills, 50).get_page(request.GET.get("sayfa"))
    open_qs = services.open_bills(site)
    today = timezone.localdate()
    return render(
        request,
        "expenses/bill_list.html",
        {
            "site": site,
            "page": page,
            "form": form,
            "show": show,
            "today": today,
            "open_total": sum(b.remaining for b in open_qs),
            "overdue_total": sum(b.remaining for b in open_qs if b.due_date and b.due_date < today),
            "can_manage": can_manage,
        },
    )


@site_view("finance.view")
def bill_detail(request, site, bill_id: int):
    bill = get_object_or_404(Bill.objects.select_related("category", "vendor"), pk=bill_id, site=site)
    can_manage = has_perm(request, "expenses.manage", site.pk)
    form = BillPaymentForm(site=site, bill=bill)
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied
        form = BillPaymentForm(request.POST, site=site)
        if form.is_valid():
            d = form.cleaned_data
            try:
                services.pay_bill(bill, day=d["date"], paid_from=d["paid_from"], amount=d["amount"], user=request.user)
            except services.ExpenseError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Ödeme kaydedildi.")
                return redirect("expenses:bill_detail", site_id=site.pk, bill_id=bill.pk)
    return render(
        request,
        "expenses/bill_detail.html",
        {
            "site": site,
            "bill": bill,
            "payments": bill.payments.select_related("paid_from"),
            "form": form,
            "can_manage": can_manage,
            "cancel_form": CancelForm(),
        },
    )


@site_view("expenses.manage")
def bill_cancel(request, site, bill_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    bill = get_object_or_404(Bill, pk=bill_id, site=site)
    form = CancelForm(request.POST)
    if form.is_valid():
        try:
            services.cancel_bill(bill, request.user, form.cleaned_data["reason"])
            messages.success(request, "Fatura iptal edildi; ters kayıt atıldı.")
        except services.ExpenseError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "İptal nedeni yazın.")
    return redirect("expenses:bill_detail", site_id=site.pk, bill_id=bill.pk)


@site_view("expenses.manage")
def bill_payment_cancel(request, site, payment_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    payment = get_object_or_404(BillPayment, pk=payment_id, bill__site=site)
    form = CancelForm(request.POST)
    if form.is_valid():
        try:
            services.cancel_bill_payment(payment, request.user, form.cleaned_data["reason"])
            messages.success(request, "Ödeme iptal edildi; ters kayıt atıldı.")
        except services.ExpenseError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "İptal nedeni yazın.")
    return redirect("expenses:bill_detail", site_id=site.pk, bill_id=payment.bill_id)
