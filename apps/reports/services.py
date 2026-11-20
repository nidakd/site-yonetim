"""Okuma tarafı: bölüm bakiyesi ve ekstresi, borç listesi, site özeti. Tüm rakamlar defterden gelir."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db.models import Q, Sum
from django.utils import timezone

from apps.billing.services import open_charges
from apps.ledger.models import Account, JournalLine
from apps.ledger.services import account_balances, cash_accounts, unit_lines
from apps.property.models import Unit

RESIDENT_ROLES = Q(account__role=Account.Role.RECEIVABLES) | Q(account__role=Account.Role.ADVANCES)


def unit_balances(site, unit_ids=None) -> dict[int, int]:
    """Bölüm → net bakiye (pozitif: borçlu, negatif: avanslı)."""
    qs = JournalLine.objects.filter(RESIDENT_ROLES, account__site=site, unit__isnull=False)
    if unit_ids is not None:
        qs = qs.filter(unit_id__in=unit_ids)
    return {
        row["unit_id"]: (row["d"] or 0) - (row["c"] or 0)
        for row in qs.values("unit_id").annotate(d=Sum("debit"), c=Sum("credit"))
    }


@dataclass
class StatementRow:
    date: date
    description: str
    debit: int
    credit: int
    balance: int
    source_type: str
    source_id: int | None


def unit_statement(unit: Unit, *, limit: int | None = None, start: date | None = None) -> list[StatementRow]:
    """Bölüm ekstresi: her yevmiye kaydının bölüme net etkisi ve yürüyen bakiye.

    Avans mahsubu gibi bölüm bakiyesini değiştirmeyen iç kayıtlar (net 0) gösterilmez."""
    totals = (
        unit_lines(unit)
        .values("entry_id", "entry__date", "entry__description", "entry__source_type", "entry__source_id")
        .annotate(d=Sum("debit"), c=Sum("credit"))
        .order_by("entry__date", "entry_id")
    )
    rows, running = [], 0
    for t in totals:
        net = (t["d"] or 0) - (t["c"] or 0)
        if net == 0:
            continue
        running += net
        rows.append(
            StatementRow(
                t["entry__date"],
                t["entry__description"],
                max(net, 0),
                max(-net, 0),
                running,
                t["entry__source_type"],
                t["entry__source_id"],
            )
        )
    if start is not None:
        opening = next((r.balance - r.debit + r.credit for r in rows if r.date >= start), running)
        rows = [r for r in rows if r.date >= start]
        if opening:
            rows.insert(0, StatementRow(start, "Devreden bakiye", 0, 0, opening, "", None))
    return rows[-limit:] if limit else rows


def unit_open_charges(unit: Unit):
    return open_charges(unit)


def debtors(site, *, as_of: date | None = None):
    """Borçlu bölümler: net bakiye > 0, en eski açık borcun vadesi ve gecikme günü."""
    as_of = as_of or timezone.localdate()
    balances = {uid: b for uid, b in unit_balances(site).items() if b > 0}
    units = {u.pk: u for u in Unit.objects.filter(pk__in=balances).select_related("block")}
    oldest: dict[int, date] = {}
    for c in open_charges(site=site):
        if c.unit_id in balances:
            oldest[c.unit_id] = min(oldest.get(c.unit_id, c.due_date), c.due_date)
    rows = []
    for uid, bal in balances.items():
        due = oldest.get(uid)
        rows.append(
            {
                "unit": units[uid],
                "balance": bal,
                "oldest_due": due,
                "days_late": max((as_of - due).days, 0) if due else 0,
            }
        )
    return sorted(rows, key=lambda r: (-r["days_late"], -r["balance"]))


def site_summary(site, *, today: date | None = None) -> dict:
    from apps.billing.models import Charge
    from apps.payments.models import Payment

    today = today or timezone.localdate()
    month = today.replace(day=1)
    balances = account_balances(site)
    cash = [(a, balances.get(a.pk, 0)) for a in cash_accounts(site)]
    accrued = (
        Charge.objects.filter(site=site, period=month, is_cancelled=False)
        .exclude(batch__method="opening")
        .aggregate(s=Sum("amount"))["s"]
        or 0
    )
    collected = (
        Payment.objects.filter(site=site, status=Payment.Status.VALID, date__gte=month, date__lte=today)
        .exclude(channel=Payment.Channel.OPENING)
        .aggregate(s=Sum("amount"))["s"]
        or 0
    )
    unit_bal = unit_balances(site)
    return {
        "unit_count": Unit.objects.filter(site=site, is_archived=False).count(),
        "month": month,
        "accrued": accrued,
        "collected": collected,
        "receivable": sum(b for b in unit_bal.values() if b > 0),
        "advance": -sum(b for b in unit_bal.values() if b < 0),
        "debtor_count": sum(1 for b in unit_bal.values() if b > 0),
        "cash": cash,
        "cash_total": sum(b for _, b in cash),
    }
