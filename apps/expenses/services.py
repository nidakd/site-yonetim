"""Gider, virman ve kasa/banka/gider hesabı açma."""

from __future__ import annotations

from datetime import date

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.core.utils import reserve_id
from apps.ledger import services as ledger
from apps.ledger.models import Account

from .models import Expense, Transfer


class ExpenseError(ValueError):
    pass


def _next_code(site, prefix: str) -> str:
    codes = Account.objects.filter(site=site, code__startswith=f"{prefix}.").values_list("code", flat=True)
    numbers = [int(c.split(".")[1]) for c in codes if c.split(".")[1].isdigit()]
    nxt = max([n for n in numbers if n < 99] or [0]) + 1
    if nxt >= 99:
        raise ExpenseError("Bu grupta yeni hesap kodu kalmadı.")
    return f"{prefix}.{nxt:02d}"


def create_account(site, *, name: str, group: str, iban: str = "") -> Account:
    """group: 'cash' | 'bank' | 'expense'."""
    spec = {
        "cash": ("100", Account.Kind.ASSET, Account.CashKind.CASH),
        "bank": ("102", Account.Kind.ASSET, Account.CashKind.BANK),
        "expense": ("770", Account.Kind.EXPENSE, None),
    }[group]
    prefix, kind, cash_kind = spec
    with transaction.atomic():
        account = Account.objects.create(
            site=site,
            code=_next_code(site, prefix),
            name=name,
            kind=kind,
            cash_kind=cash_kind,
            iban=iban.replace(" ", "").upper(),
        )
        record("account.created", account, {"kod": account.code, "ad": name}, site_id=site.pk)
    return account


def _check_accounts(site, *accounts):
    for acc in accounts:
        if acc.site_id != site.pk:
            raise ExpenseError("Hesap bu siteye ait değil.")


def create_expense(
    site,
    *,
    day: date,
    category: Account,
    paid_from: Account,
    amount: int,
    description: str,
    user,
    vendor=None,
    document_number: str = "",
) -> Expense:
    _check_accounts(site, category, paid_from)
    if category.kind != Account.Kind.EXPENSE or not paid_from.cash_kind:
        raise ExpenseError("Gider kalemi bir gider hesabı, ödeme hesabı kasa ya da banka olmalı.")
    if vendor is not None and vendor.site_id != site.pk:
        raise ExpenseError("Tedarikçi bu siteye ait değil.")
    with transaction.atomic():
        expense_id = reserve_id(Expense)
        try:
            entry = ledger.post(
                site,
                day,
                description,
                [
                    ledger.Line(category, debit=amount, memo=description),
                    ledger.Line(paid_from, credit=amount, memo=description),
                ],
                source_type="expense",
                source_id=expense_id,
            )
        except ledger.LedgerError as exc:
            raise ExpenseError(str(exc)) from exc
        expense = Expense.objects.create(
            id=expense_id,
            site=site,
            date=day,
            category=category,
            paid_from=paid_from,
            amount=amount,
            description=description,
            vendor=vendor,
            document_number=document_number,
            entry=entry,
            created_by=user,
        )
        record("expense.created", expense, {"tutar": amount, "kalem": category.code}, site_id=site.pk)
    return expense


def cancel_expense(expense: Expense, user, reason: str, day: date | None = None) -> Expense:
    day = day or timezone.localdate()
    with transaction.atomic():
        expense = Expense.objects.select_for_update().select_related("entry", "site").get(pk=expense.pk)
        if expense.status != Expense.Status.VALID:
            raise ExpenseError("Bu gider zaten iptal edilmiş.")
        try:
            expense.cancel_entry = ledger.reverse(expense.entry, day, f"Gider iptali: {expense.description}")
        except ledger.LedgerError as exc:
            raise ExpenseError(str(exc)) from exc
        expense.status, expense.cancel_reason = Expense.Status.CANCELLED, reason
        expense.save()
        record("expense.cancelled", expense, {"neden": reason}, site_id=expense.site_id)
    return expense


def create_transfer(
    site, *, day: date, from_account: Account, to_account: Account, amount: int, user, description: str = ""
) -> Transfer:
    _check_accounts(site, from_account, to_account)
    if not (from_account.cash_kind and to_account.cash_kind) or from_account.pk == to_account.pk:
        raise ExpenseError("Virman iki farklı kasa/banka hesabı arasında yapılır.")
    text = description or f"Virman: {from_account.name} → {to_account.name}"
    with transaction.atomic():
        transfer_id = reserve_id(Transfer)
        try:
            entry = ledger.post(
                site,
                day,
                text,
                [ledger.Line(to_account, debit=amount, memo=text), ledger.Line(from_account, credit=amount, memo=text)],
                source_type="transfer",
                source_id=transfer_id,
            )
        except ledger.LedgerError as exc:
            raise ExpenseError(str(exc)) from exc
        transfer = Transfer.objects.create(
            id=transfer_id,
            site=site,
            date=day,
            from_account=from_account,
            to_account=to_account,
            amount=amount,
            description=text,
            entry=entry,
            created_by=user,
        )
        record(
            "transfer.created",
            transfer,
            {"tutar": amount, "cikan": from_account.code, "giren": to_account.code},
            site_id=site.pk,
        )
    return transfer
