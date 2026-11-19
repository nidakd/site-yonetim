"""Tahsilat: ödeme al, borçlarla eşleştir (FIFO + öncelik), artanı avans yap, iptal et."""

from __future__ import annotations

from collections.abc import Collection
from datetime import date

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.audit.services import record
from apps.billing.services import open_charges
from apps.core.utils import reserve_id
from apps.ledger import services as ledger
from apps.ledger.models import Account
from domain.allocation import OpenItem, allocate

from .models import Allocation, Payment, ReceiptSequence


class PaymentError(ValueError):
    pass


def next_receipt_number(site, year: int) -> int:
    seq, _ = ReceiptSequence.objects.select_for_update().get_or_create(site=site, year=year)
    seq.last_number += 1
    seq.save(update_fields=["last_number", "updated_at"])
    return seq.last_number


def _open_items(unit):
    charges = open_charges(unit)
    items = [OpenItem(c.pk, c.due_date, c.charge_type.priority, c.remaining, c.pk) for c in charges]
    return {c.pk: c for c in charges}, items


def create_payment(
    site,
    unit,
    *,
    amount: int,
    day: date,
    channel: str,
    account: Account,
    user,
    payer=None,
    reference: str = "",
    note: str = "",
    preferred: Collection[int] = (),
) -> Payment:
    if unit.site_id != site.pk:
        raise PaymentError("Bölüm bu siteye ait değil.")
    if account.site_id != site.pk or not account.cash_kind:
        raise PaymentError("Tahsilat yalnızca bu sitenin kasa ya da banka hesabına alınabilir.")
    if amount <= 0:
        raise PaymentError("Tutar sıfırdan büyük olmalı.")
    try:
        ledger.check_open(site, day)
    except ledger.PeriodLocked as exc:
        raise PaymentError(str(exc)) from exc

    with transaction.atomic():
        charges, items = _open_items(unit)
        result = allocate(amount, items, preferred=set(preferred))
        payment_id = reserve_id(Payment)
        receivables = ledger.system_account(site, Account.Role.RECEIVABLES)
        advances = ledger.system_account(site, Account.Role.ADVANCES)
        description = f"Tahsilat {unit.label}"
        lines = [
            ledger.Line(account, debit=amount, unit=unit, person=payer, memo=description),
            ledger.Line(receivables, credit=result.allocated, unit=unit, person=payer, memo=description),
            ledger.Line(advances, credit=result.leftover, unit=unit, person=payer, memo="Avans (fazla ödeme)"),
        ]
        entry = ledger.post(site, day, description, lines, source_type="payment", source_id=payment_id)
        payment = Payment.objects.create(
            id=payment_id,
            site=site,
            unit=unit,
            payer=payer,
            payer_name=payer.full_name if payer else "",
            date=day,
            amount=amount,
            channel=channel,
            account=account,
            reference=reference,
            note=note,
            receipt_year=day.year,
            receipt_number=next_receipt_number(site, day.year),
            entry=entry,
            created_by=user,
        )
        Allocation.objects.bulk_create(
            [
                Allocation(organization_id=payment.organization_id, payment=payment, charge_id=cid, amount=take)
                for cid, take in result.lines
            ]
        )
        record(
            "payment.created",
            payment,
            {"makbuz": payment.receipt_no, "tutar": amount, "avans": result.leftover},
            site_id=site.pk,
        )
    return payment


def unallocated(payment: Payment) -> int:
    used = payment.allocations.filter(is_active=True).aggregate(s=Sum("amount"))["s"] or 0
    return payment.amount - used


def advance_balance(unit) -> int:
    """Bölümün borçlarla eşleşmemiş geçerli ödemeleri (avans)."""
    payments = Payment.objects.filter(unit=unit, status=Payment.Status.VALID).annotate(
        used=Sum("allocations__amount", filter=Q(allocations__is_active=True))
    )
    return sum(p.amount - (p.used or 0) for p in payments)


def apply_advances(site, units, *, day: date) -> int:
    """Avansı olan bölümlerde açık borçları avanstan kapatır. Mahsup edilen toplamı döndürür."""
    receivables = ledger.system_account(site, Account.Role.RECEIVABLES)
    advances = ledger.system_account(site, Account.Role.ADVANCES)
    applied_total = 0
    for unit in {u.pk: u for u in units}.values():
        payments = list(
            Payment.objects.filter(unit=unit, status=Payment.Status.VALID)
            .annotate(used=Sum("allocations__amount", filter=Q(allocations__is_active=True)))
            .order_by("date", "id")
        )
        payments = [p for p in payments if p.amount - (p.used or 0) > 0]
        if not payments:
            continue
        _, items = _open_items(unit)
        if not items:
            continue
        new_allocs, remaining = [], {i.id: i.remaining for i in items}
        entry_day = day
        for p in payments:
            open_now = [OpenItem(i.id, i.due_date, i.priority, remaining[i.id], i.sort_id) for i in items]
            result = allocate(p.amount - (p.used or 0), open_now)
            for cid, take in result.lines:
                remaining[cid] -= take
                new_allocs.append(
                    Allocation(
                        organization_id=p.organization_id, payment=p, charge_id=cid, amount=take, from_advance=True
                    )
                )
                entry_day = max(entry_day, p.date)
        total = sum(a.amount for a in new_allocs)
        if total == 0:
            continue
        memo = "Avanstan mahsup"
        ledger.post(
            site,
            entry_day,
            f"Avans mahsubu {unit.label}",
            [
                ledger.Line(advances, debit=total, unit=unit, memo=memo),
                ledger.Line(receivables, credit=total, unit=unit, memo=memo),
            ],
            source_type="advance",
            source_id=unit.pk,
        )
        Allocation.objects.bulk_create(new_allocs)
        record("advance.applied", unit, {"tutar": total}, site_id=site.pk)
        applied_total += total
    return applied_total


def cancel_payment(payment: Payment, user, reason: str, day: date | None = None) -> Payment:
    """Ödemeyi iptal eder: eşleşmeler geri alınır, borçlar yeniden açılır, kasa/banka geri düşer.
    Makbuz numarası boşluk bırakmamak için kalır; makbuz "İPTAL" olarak görünür."""
    day = day or timezone.localdate()
    with transaction.atomic():
        payment = Payment.objects.select_for_update().select_related("site", "unit", "account").get(pk=payment.pk)
        if payment.status != Payment.Status.VALID:
            raise PaymentError("Bu tahsilat zaten iptal edilmiş.")
        site, unit = payment.site, payment.unit
        allocated = payment.amount - unallocated(payment)
        description = f"Tahsilat iptali {payment.receipt_no} {unit.label}"
        try:
            entry = ledger.post(
                site,
                day,
                description,
                [
                    ledger.Line(ledger.system_account(site, Account.Role.RECEIVABLES), debit=allocated, unit=unit),
                    ledger.Line(
                        ledger.system_account(site, Account.Role.ADVANCES), debit=payment.amount - allocated, unit=unit
                    ),
                    ledger.Line(payment.account, credit=payment.amount, unit=unit, memo=description),
                ],
                source_type="payment",
                source_id=payment.pk,
            )
        except ledger.LedgerError as exc:
            raise PaymentError(str(exc)) from exc
        payment.allocations.filter(is_active=True).update(is_active=False, reversed_at=timezone.now())
        payment.status = Payment.Status.CANCELLED
        payment.cancel_entry = entry
        payment.cancelled_at, payment.cancelled_by, payment.cancel_reason = timezone.now(), user, reason
        payment.save()
        record("payment.cancelled", payment, {"makbuz": payment.receipt_no, "neden": reason}, site_id=site.pk)
    return payment
