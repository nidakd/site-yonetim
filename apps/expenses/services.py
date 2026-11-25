"""Gider, virman ve kasa/banka/gider hesabı açma."""

from __future__ import annotations

from datetime import date

from django.db import models, transaction
from django.db.models import F
from django.utils import timezone

from apps.audit.services import record
from apps.core.utils import reserve_id
from apps.ledger import services as ledger
from apps.ledger.models import Account
from domain.bills import BillError, check_payment

from .models import Bill, BillPayment, Expense, Transfer


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


# --- ödenecek faturalar -----------------------------------------------------------------------


def create_bill(
    site,
    *,
    day: date,
    category: Account,
    amount: int,
    description: str,
    user,
    due_date: date | None = None,
    vendor=None,
    document_number: str = "",
) -> Bill:
    """Gelen faturayı kaydeder: gider bugün yazılır, para ödenince çıkar."""
    _check_accounts(site, category)
    if category.kind != Account.Kind.EXPENSE:
        raise ExpenseError("Gider kalemi bir gider hesabı olmalı.")
    if vendor is not None and vendor.site_id != site.pk:
        raise ExpenseError("Tedarikçi bu siteye ait değil.")
    if amount <= 0:
        raise ExpenseError("Tutar sıfırdan büyük olmalı.")
    if due_date and due_date < day:
        raise ExpenseError("Son ödeme tarihi fatura tarihinden önce olamaz.")
    payables = ledger.system_account(site, Account.Role.PAYABLES)
    with transaction.atomic():
        bill_id = reserve_id(Bill)
        try:
            entry = ledger.post(
                site,
                day,
                f"Fatura: {description}",
                [
                    ledger.Line(category, debit=amount, memo=description),
                    ledger.Line(payables, credit=amount, memo=description),
                ],
                source_type="bill",
                source_id=bill_id,
            )
        except ledger.LedgerError as exc:
            raise ExpenseError(str(exc)) from exc
        bill = Bill.objects.create(
            id=bill_id,
            site=site,
            date=day,
            due_date=due_date,
            category=category,
            vendor=vendor,
            description=description,
            document_number=document_number,
            amount=amount,
            entry=entry,
            created_by=user,
        )
        record("bill.created", bill, {"tutar": amount, "kalem": category.code}, site_id=site.pk)
    return bill


def pay_bill(bill: Bill, *, day: date, paid_from: Account, amount: int, user) -> BillPayment:
    """Faturanın tamamını ya da bir kısmını kasa/bankadan öder."""
    with transaction.atomic():
        bill = Bill.objects.select_for_update().select_related("site").get(pk=bill.pk)
        site = bill.site
        _check_accounts(site, paid_from)
        if not paid_from.cash_kind:
            raise ExpenseError("Ödeme hesabı kasa ya da banka olmalı.")
        if bill.status != Bill.Status.VALID:
            raise ExpenseError("İptal edilmiş fatura ödenemez.")
        if day < bill.date:
            raise ExpenseError("Ödeme tarihi fatura tarihinden önce olamaz.")
        try:
            check_payment(bill.amount, [bill.paid_amount], amount)
        except BillError as exc:
            raise ExpenseError(str(exc)) from exc
        payables = ledger.system_account(site, Account.Role.PAYABLES)
        payment_id = reserve_id(BillPayment)
        text = f"Fatura ödemesi: {bill.description}"
        try:
            entry = ledger.post(
                site,
                day,
                text,
                [ledger.Line(payables, debit=amount, memo=text), ledger.Line(paid_from, credit=amount, memo=text)],
                source_type="bill_payment",
                source_id=payment_id,
            )
        except ledger.LedgerError as exc:
            raise ExpenseError(str(exc)) from exc
        payment = BillPayment.objects.create(
            id=payment_id, bill=bill, date=day, paid_from=paid_from, amount=amount, entry=entry, created_by=user
        )
        bill.paid_amount += amount
        bill.save(update_fields=["paid_amount"])
        record("bill.paid", bill, {"tutar": amount, "hesap": paid_from.code}, site_id=site.pk)
    return payment


def cancel_bill_payment(payment: BillPayment, user, reason: str, day: date | None = None) -> BillPayment:
    day = day or timezone.localdate()
    with transaction.atomic():
        payment = BillPayment.objects.select_for_update().select_related("entry", "bill").get(pk=payment.pk)
        bill = Bill.objects.select_for_update().get(pk=payment.bill_id)
        if payment.status != BillPayment.Status.VALID:
            raise ExpenseError("Bu ödeme zaten iptal edilmiş.")
        try:
            payment.cancel_entry = ledger.reverse(payment.entry, day, f"Fatura ödemesi iptali: {bill.description}")
        except ledger.LedgerError as exc:
            raise ExpenseError(str(exc)) from exc
        payment.status, payment.cancel_reason = BillPayment.Status.CANCELLED, reason
        payment.save()
        bill.paid_amount -= payment.amount
        bill.save(update_fields=["paid_amount"])
        record("bill_payment.cancelled", payment, {"neden": reason, "tutar": payment.amount}, site_id=bill.site_id)
    return payment


def cancel_bill(bill: Bill, user, reason: str, day: date | None = None) -> Bill:
    """Ödemesi olmayan faturayı iptal eder (ters kayıt). Ödemesi varsa önce ödemeler iptal edilmeli."""
    day = day or timezone.localdate()
    with transaction.atomic():
        bill = Bill.objects.select_for_update().select_related("entry").get(pk=bill.pk)
        if bill.status != Bill.Status.VALID:
            raise ExpenseError("Bu fatura zaten iptal edilmiş.")
        if bill.paid_amount:
            raise ExpenseError("Faturanın ödemesi var; önce ödemeyi iptal edin.")
        try:
            bill.cancel_entry = ledger.reverse(bill.entry, day, f"Fatura iptali: {bill.description}")
        except ledger.LedgerError as exc:
            raise ExpenseError(str(exc)) from exc
        bill.status, bill.cancel_reason = Bill.Status.CANCELLED, reason
        bill.save()
        record("bill.cancelled", bill, {"neden": reason}, site_id=bill.site_id)
    return bill


def open_bills(site):
    return (
        Bill.objects.filter(site=site, status=Bill.Status.VALID, paid_amount__lt=F("amount"))
        .select_related("category", "vendor")
        .order_by(models.F("due_date").asc(nulls_last=True), "date", "id")
    )
