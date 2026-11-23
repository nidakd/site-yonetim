"""Geçmiş ayların borçlarını Excel'den aktarma: ay başına bir tahakkuk (yöntem: Excel'den aktarım).

Sihirbazdaki tahakkukla aynı defter kaydı (120 Sakin alacakları / gelir hesabı) ve aynı kural:
aynı daire aynı ay aynı türden iki kez borçlandırılamaz. İptal, normal tahakkuk iptalidir.
"""

from __future__ import annotations

from datetime import date

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.core.utils import reserve_id
from apps.ledger import services as ledger
from apps.ledger.models import Account

from .models import Charge, ChargeBatch, ChargeType
from .services import BillingError, responsible_on


def post_month(
    site, charge_type: ChargeType, period: date, due: date, rows: list[tuple], user, description: str
) -> ChargeBatch | None:
    """rows: [(daire, tutar)] — tutarı sıfır olanlar atlanır."""
    rows = [(unit, amount) for unit, amount in rows if amount > 0]
    if not rows:
        return None
    try:
        ledger.check_open(site, period)
    except ledger.PeriodLocked as exc:
        raise BillingError(str(exc)) from exc
    receivables = ledger.system_account(site, Account.Role.RECEIVABLES)
    total = sum(a for _, a in rows)
    with transaction.atomic():
        batch_id = reserve_id(ChargeBatch)
        lines = [ledger.Line(receivables, debit=a, unit=u, memo=description) for u, a in rows]
        lines.append(ledger.Line(charge_type.income_account, credit=total, memo=description))
        try:
            entry = ledger.post(site, period, description, lines, source_type="charge_batch", source_id=batch_id)
        except ledger.LedgerError as exc:
            raise BillingError(str(exc)) from exc
        now = timezone.now()
        batch = ChargeBatch.objects.create(
            id=batch_id,
            site=site,
            charge_type=charge_type,
            period=period,
            due_date=due,
            method=ChargeBatch.Method.IMPORT,
            description=description,
            status=ChargeBatch.Status.APPROVED,
            total=total,
            unit_count=len(rows),
            entry=entry,
            created_by=user,
            approved_at=now,
            approved_by=user,
        )
        responsible = responsible_on([u for u, _ in rows], period)
        Charge.objects.bulk_create(
            [
                Charge(
                    organization_id=batch.organization_id,
                    site=site,
                    unit=u,
                    charge_type=charge_type,
                    batch=batch,
                    period=period,
                    due_date=due,
                    amount=a,
                    base_amount=a,
                    description=description,
                    responsible=responsible.get(u.pk, []),
                    period_unique=charge_type.once_per_period,
                )
                for u, a in rows
            ]
        )
        record("batch.approved", batch, {"donem": str(period), "toplam": total, "kaynak": "excel"}, site_id=site.pk)
    return batch


def transfer_account(site) -> Account:
    """Excel'den aktarılan geçmiş tahsilatların yazıldığı ayrı kasa hesabı.

    Geçmiş giderler aktarılmadığı için bu tahsilatlar gerçek Kasa/Banka bakiyesini şişirmesin diye
    ayrı tutulur; yönetici gerçek bakiyeyi bildiğinde virman/gider ile kapatır."""
    account, _ = Account.objects.get_or_create(
        site=site,
        code="100.90",
        defaults={
            "name": "Geçmiş tahsilatlar (Excel aktarımı)",
            "kind": Account.Kind.ASSET,
            "cash_kind": Account.CashKind.CASH,
        },
    )
    return account
