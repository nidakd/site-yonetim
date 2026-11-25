"""Deftere yazan tek yer. Diğer modüller kayıt üretmek için yalnızca `post` ve `reverse` çağırır."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.db.models import Q, Sum

from apps.audit.services import record
from apps.core import context

from .models import Account, JournalEntry, JournalLine, PeriodLock


class LedgerError(ValueError):
    pass


class PeriodLocked(LedgerError):
    pass


# (kod, ad, tür, sistem rolü, kasa/banka)
DEFAULT_CHART = [
    ("100", "Kasa", Account.Kind.ASSET, None, Account.CashKind.CASH),
    ("102.01", "Banka", Account.Kind.ASSET, None, Account.CashKind.BANK),
    ("120", "Sakin alacakları", Account.Kind.ASSET, Account.Role.RECEIVABLES, None),
    ("320", "Satıcılara borçlar", Account.Kind.LIABILITY, Account.Role.PAYABLES, None),
    ("340", "Alınan avanslar", Account.Kind.LIABILITY, Account.Role.ADVANCES, None),
    ("500", "Açılış bakiyeleri", Account.Kind.EQUITY, Account.Role.OPENING, None),
    ("600", "Aidat gelirleri", Account.Kind.INCOME, None, None),
    ("601", "Yakıt gelirleri", Account.Kind.INCOME, None, None),
    ("602", "Özel toplama gelirleri", Account.Kind.INCOME, None, None),
    ("649", "Diğer gelirler", Account.Kind.INCOME, None, None),
    ("770.01", "Elektrik", Account.Kind.EXPENSE, None, None),
    ("770.02", "Su", Account.Kind.EXPENSE, None, None),
    ("770.03", "Doğalgaz ve yakıt", Account.Kind.EXPENSE, None, None),
    ("770.04", "Temizlik hizmeti", Account.Kind.EXPENSE, None, None),
    ("770.05", "Bakım ve onarım", Account.Kind.EXPENSE, None, None),
    ("770.06", "Asansör", Account.Kind.EXPENSE, None, None),
    ("770.07", "Personel", Account.Kind.EXPENSE, None, None),
    ("770.08", "Sigorta", Account.Kind.EXPENSE, None, None),
    ("770.09", "Banka masrafları", Account.Kind.EXPENSE, None, None),
    ("770.10", "Temizlik malzemesi", Account.Kind.EXPENSE, None, None),
    ("770.11", "Bahçe ve peyzaj", Account.Kind.EXPENSE, None, None),
    ("770.12", "Yönetim ücreti", Account.Kind.EXPENSE, None, None),
    ("770.99", "Diğer giderler", Account.Kind.EXPENSE, None, None),
]


def _free_code(site, code: str) -> str:
    taken = set(Account.objects.filter(site=site).values_list("code", flat=True))
    if code not in taken:
        return code
    prefix = code.split(".")[0]
    for n in range(1, 99):
        candidate = f"{prefix}.{n:02d}"
        if candidate not in taken:
            return candidate
    raise LedgerError("Bu grupta yeni hesap kodu kalmadı.")


def _complete_chart(site) -> list[Account]:
    """Sonradan hazır plana eklenen hesapları eski sitelere ekler (rol ya da ada göre; kod doluysa boş kod)."""
    existing = list(Account.objects.filter(site=site).values_list("role", "name"))
    roles = {r for r, _ in existing if r}
    names = {n.casefold() for _, n in existing}
    added = []
    for code, name, kind, role, cash_kind in DEFAULT_CHART:
        if (role and role in roles) or (not role and (cash_kind or name.casefold() in names)):
            continue
        if not role and kind != Account.Kind.EXPENSE:
            continue
        if name == "Temizlik hizmeti" and "temizlik" in names:  # eski adıyla zaten var
            continue
        added.append(
            Account.objects.create(
                site=site, code=_free_code(site, code), name=name, kind=kind, role=role, cash_kind=cash_kind
            )
        )
    if added:
        record("ledger.chart_completed", site, {"eklenen": [a.code for a in added]}, site_id=site.pk)
    return added


def ensure_chart(site) -> bool:
    """Siteye hazır hesap planını kurar; kuruluysa yalnızca eksik hazır hesapları ekler. Sıfırdan kurduysa True."""
    if Account.objects.filter(site=site).exists():
        _complete_chart(site)
        return False
    Account.objects.bulk_create(
        [
            Account(
                site=site,
                code=code,
                name=name,
                kind=kind,
                role=role,
                cash_kind=cash_kind,
                organization_id=site.organization_id,
            )
            for code, name, kind, role, cash_kind in DEFAULT_CHART
        ]
    )
    record("ledger.setup", site, {"hesap_sayisi": len(DEFAULT_CHART)}, site_id=site.pk)
    return True


def system_account(site, role: str) -> Account:
    return Account.objects.get(site=site, role=role)


@dataclass(frozen=True)
class Line:
    account: Account
    debit: int = 0
    credit: int = 0
    unit: object = None
    person: object = None
    memo: str = ""


def locked_until(site) -> date | None:
    lock = PeriodLock.objects.filter(site=site).first()
    return lock.locked_until if lock else None


def check_open(site, day: date) -> None:
    until = locked_until(site)
    if until and day <= until:
        raise PeriodLocked(f"{until:%d.%m.%Y} ve öncesi kilitli; bu tarihe kayıt girilemez.")


def post(
    site,
    day: date,
    description: str,
    lines: list[Line],
    *,
    source_type: str = "",
    source_id: int | None = None,
    reverses: JournalEntry | None = None,
) -> JournalEntry:
    lines = [ln for ln in lines if ln.debit or ln.credit]
    if len(lines) < 2:
        raise LedgerError("Bir kayıtta en az iki satır olmalı.")
    for ln in lines:
        if ln.debit < 0 or ln.credit < 0 or (ln.debit and ln.credit):
            raise LedgerError("Her satır ya borç ya alacak olmalı ve tutar pozitif olmalı.")
        if ln.account.site_id != site.pk:
            raise LedgerError("Hesap bu siteye ait değil.")
        if ln.unit is not None and ln.unit.site_id != site.pk:
            raise LedgerError("Bölüm bu siteye ait değil.")
    if sum(ln.debit for ln in lines) != sum(ln.credit for ln in lines):
        raise LedgerError("Kayıt dengesiz: borç ve alacak toplamı eşit değil.")
    check_open(site, day)
    with transaction.atomic():
        entry = JournalEntry.objects.create(
            site=site,
            date=day,
            description=description[:255],
            source_type=source_type,
            source_id=source_id,
            reverses=reverses,
            created_by_id=context.current_actor_id(),
        )
        JournalLine.objects.bulk_create(
            [
                JournalLine(
                    organization_id=entry.organization_id,
                    entry=entry,
                    account=ln.account,
                    unit=ln.unit,
                    person=ln.person,
                    debit=ln.debit,
                    credit=ln.credit,
                    memo=ln.memo[:255],
                )
                for ln in lines
            ]
        )
    return entry


def reverse(entry: JournalEntry, day: date, description: str = "") -> JournalEntry:
    """Kaydın ters kaydını atar (borç ↔ alacak). Bir kayıt yalnızca bir kez ters çevrilebilir."""
    if JournalEntry.objects.filter(reverses=entry).exists():
        raise LedgerError("Bu kayıt zaten ters çevrilmiş.")
    lines = [
        Line(ln.account, debit=ln.credit, credit=ln.debit, unit=ln.unit, person=ln.person, memo=ln.memo)
        for ln in entry.lines.select_related("account", "unit")
    ]
    return post(
        entry.site,
        day,
        description or f"İptal: {entry.description}",
        lines,
        source_type=entry.source_type,
        source_id=entry.source_id,
        reverses=entry,
    )


def balance(account: Account, *, unit=None, until: date | None = None) -> int:
    """Borç − alacak (varlık/gider hesaplarında pozitif = bakiye)."""
    qs = JournalLine.objects.filter(account=account)
    if unit is not None:
        qs = qs.filter(unit=unit)
    if until is not None:
        qs = qs.filter(entry__date__lte=until)
    totals = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
    return (totals["d"] or 0) - (totals["c"] or 0)


def account_balances(site, *, until: date | None = None) -> dict[int, int]:
    """Mizan: hesap kimliği → borç − alacak."""
    qs = JournalLine.objects.filter(account__site=site)
    if until is not None:
        qs = qs.filter(entry__date__lte=until)
    return {
        row["account_id"]: (row["d"] or 0) - (row["c"] or 0)
        for row in qs.values("account_id").annotate(d=Sum("debit"), c=Sum("credit"))
    }


def lock_period(site, until: date, user) -> PeriodLock:
    current = locked_until(site)
    if current and until <= current:
        raise LedgerError(f"Dönem zaten {current:%d.%m.%Y} tarihine kadar kilitli; kilit yalnızca ileri alınabilir.")
    with transaction.atomic():
        lock, _ = PeriodLock.objects.update_or_create(site=site, defaults={"locked_until": until, "locked_by": user})
        record(
            "period.locked", site, {"tarih": str(until), "onceki": str(current) if current else None}, site_id=site.pk
        )
    return lock


def cash_accounts(site, *, active_only=True):
    qs = Account.objects.filter(site=site, cash_kind__isnull=False)
    return qs.filter(is_active=True) if active_only else qs


def income_accounts(site):
    return Account.objects.filter(site=site, kind=Account.Kind.INCOME, is_active=True)


def expense_accounts(site):
    return Account.objects.filter(site=site, kind=Account.Kind.EXPENSE, is_active=True)


def unit_lines(unit):
    """Bölümün sakin hesabı (alacak + avans) satırları."""
    return JournalLine.objects.filter(
        Q(account__role=Account.Role.RECEIVABLES) | Q(account__role=Account.Role.ADVANCES), unit=unit
    )
