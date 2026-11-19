from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render

from apps.tenancy.permissions import has_perm, site_view

from . import services
from .models import Account, JournalEntry, JournalLine


@site_view("finance.view")
def journal(request, site):
    entries = JournalEntry.objects.filter(site=site).prefetch_related("lines__account", "lines__unit__block")
    page = Paginator(entries, 30).get_page(request.GET.get("sayfa"))
    return render(request, "ledger/journal.html", {"site": site, "page": page})


@site_view("finance.view")
def trial_balance(request, site):
    """Mizan: hesap başına borç, alacak ve bakiye toplamı."""
    from apps.billing.services import setup_site

    setup_site(site)
    totals = {
        r["account_id"]: r
        for r in JournalLine.objects.filter(account__site=site)
        .values("account_id")
        .annotate(d=Sum("debit"), c=Sum("credit"))
    }
    rows = []
    for acc in Account.objects.filter(site=site):
        t = totals.get(acc.pk, {})
        d, c = t.get("d") or 0, t.get("c") or 0
        rows.append({"account": acc, "debit": d, "credit": c, "balance": d - c})
    return render(
        request,
        "ledger/trial_balance.html",
        {
            "site": site,
            "rows": rows,
            "debit": sum(r["debit"] for r in rows),
            "credit": sum(r["credit"] for r in rows),
        },
    )


@site_view("finance.view")
def account_detail(request, site, account_id: int):
    account = get_object_or_404(Account, pk=account_id, site=site)
    lines = (
        JournalLine.objects.filter(account=account)
        .select_related("entry", "unit__block")
        .order_by("entry__date", "entry_id", "id")
    )
    rows, running = [], 0
    for ln in lines:
        running += ln.debit - ln.credit
        rows.append((ln, running))
    rows.reverse()
    page = Paginator(rows, 50).get_page(request.GET.get("sayfa"))
    return render(
        request, "ledger/account_detail.html", {"site": site, "account": account, "page": page, "balance": running}
    )


@site_view("finance.view")
def settings_view(request, site):
    from apps.billing.models import ChargeType
    from apps.billing.services import setup_site
    from apps.expenses.forms import PeriodLockForm

    setup_site(site)
    form = PeriodLockForm(request.POST or None)
    if request.method == "POST":
        if not has_perm(request, "ledger.lock", site.pk):
            raise PermissionDenied
        if form.is_valid():
            try:
                services.lock_period(site, form.cleaned_data["locked_until"], request.user)
            except services.LedgerError as exc:
                form.add_error("locked_until", str(exc))
            else:
                messages.success(request, "Dönem kilitlendi.")
                return redirect("ledger:settings", site_id=site.pk)
    return render(
        request,
        "ledger/settings.html",
        {
            "site": site,
            "form": form,
            "locked_until": services.locked_until(site),
            "charge_types": ChargeType.objects.filter(site=site).select_related("income_account"),
            "expense_accounts": Account.objects.filter(site=site, kind=Account.Kind.EXPENSE),
            "can_lock": has_perm(request, "ledger.lock", site.pk),
            "can_billing": has_perm(request, "billing.manage", site.pk),
        },
    )
