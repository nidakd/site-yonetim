"""Açılış bakiyeleri: sisteme geçmeden önceki borç ve fazla ödemeler.

- Borçlar ay ay (yaşlandırmalı) girilir; her biri "Devreden borç" türünde bir borç kalemi olur,
  böylece ödemeler en eski aydan başlayarak kapatır. Defter: 120 Sakin alacakları / 500 Açılış.
- Fazla ödeme (avans) makbuzsuz bir "Açılış avansı" tahsilatı olur. Defter: 500 / 340 Avanslar.
  Yeni tahakkuk onaylandığında bu avans her avans gibi otomatik mahsup edilir.
- Tüm kayıtlar açılış tarihine yazılır; düzeltme tahakkuk/tahsilat iptaliyle (ters kayıt) yapılır.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.core.utils import reserve_id
from apps.ledger import services as ledger
from apps.ledger.models import Account

from .models import Charge, ChargeBatch, ChargeType
from .services import BillingError

OPENING_TYPE_NAME = "Devreden borç"


@dataclass(frozen=True)
class OpeningDebt:
    unit: object
    period: date  # ayın ilk günü
    amount: int
    description: str = ""


@dataclass(frozen=True)
class OpeningAdvance:
    unit: object
    amount: int


@dataclass
class OpeningResult:
    batch: ChargeBatch | None
    advances: list


def opening_charge_type(site) -> ChargeType:
    """Açılış borçlarının türü: gelir değil 500 Açılış bakiyeleri hesabına yazılır."""
    charge_type = ChargeType.objects.filter(site=site, name=OPENING_TYPE_NAME).first()
    if charge_type is None:
        charge_type = ChargeType.objects.create(
            site=site,
            name=OPENING_TYPE_NAME,
            income_account=ledger.system_account(site, Account.Role.OPENING),
            priority=5,
            apply_agreements=False,
            once_per_period=False,
        )
    return charge_type


def due_for(period: date, day: date) -> date:
    return min(period.replace(day=10), day)


def post_opening(
    site, *, day: date, debts: list[OpeningDebt], advances: list[OpeningAdvance], user, description: str = ""
) -> OpeningResult:
    from apps.payments.models import Payment
    from apps.payments.services import apply_advances

    debts = [d for d in debts if d.amount]
    advances = [a for a in advances if a.amount]
    if not debts and not advances:
        raise BillingError("Açılış bakiyesi yok.")
    for item in [*debts, *advances]:
        if item.unit.site_id != site.pk:
            raise BillingError("Bölüm bu siteye ait değil.")
        if item.amount < 0:
            raise BillingError("Açılış tutarları pozitif olmalı.")
    for d in debts:
        if d.period > day:
            raise BillingError(f"{d.unit.label}: borç dönemi açılış tarihinden sonra olamaz.")
    both = {d.unit.pk for d in debts} & {a.unit.pk for a in advances}
    if both:
        raise BillingError("Aynı bölümde hem açılış borcu hem açılış avansı olamaz; net tutarı girin.")
    try:
        ledger.check_open(site, day)
    except ledger.PeriodLocked as exc:
        raise BillingError(str(exc)) from exc

    description = description or f"Açılış bakiyeleri {day:%d.%m.%Y}"
    opening = ledger.system_account(site, Account.Role.OPENING)
    batch = None
    created_advances = []
    with transaction.atomic():
        if debts:
            charge_type = opening_charge_type(site)
            batch_id = reserve_id(ChargeBatch)
            receivables = ledger.system_account(site, Account.Role.RECEIVABLES)
            total = sum(d.amount for d in debts)
            lines = [
                ledger.Line(receivables, debit=d.amount, unit=d.unit, memo=d.description or "Devreden borç")
                for d in debts
            ]
            lines.append(ledger.Line(opening, credit=total, memo=description))
            try:
                entry = ledger.post(site, day, description, lines, source_type="charge_batch", source_id=batch_id)
            except ledger.LedgerError as exc:
                raise BillingError(str(exc)) from exc
            now = timezone.now()
            batch = ChargeBatch.objects.create(
                id=batch_id,
                site=site,
                charge_type=charge_type,
                period=day.replace(day=1),
                due_date=day,
                method=ChargeBatch.Method.OPENING,
                description=description,
                status=ChargeBatch.Status.APPROVED,
                total=total,
                unit_count=len({d.unit.pk for d in debts}),
                entry=entry,
                created_by=user,
                approved_at=now,
                approved_by=user,
            )
            Charge.objects.bulk_create(
                [
                    Charge(
                        organization_id=batch.organization_id,
                        site=site,
                        unit=d.unit,
                        charge_type=charge_type,
                        batch=batch,
                        period=d.period,
                        due_date=due_for(d.period, day),
                        amount=d.amount,
                        base_amount=d.amount,
                        description=d.description or f"Devreden borç {d.period:%m.%Y}",
                        period_unique=False,
                    )
                    for d in debts
                ]
            )
            record("opening.debts", batch, {"toplam": total, "kalem": len(debts)}, site_id=site.pk)

        advances_account = ledger.system_account(site, Account.Role.ADVANCES)
        for adv in advances:
            payment_id = reserve_id(Payment)
            memo = "Açılış avansı (devreden fazla ödeme)"
            try:
                entry = ledger.post(
                    site,
                    day,
                    f"Açılış avansı {adv.unit.label}",
                    [
                        ledger.Line(opening, debit=adv.amount, unit=adv.unit, memo=memo),
                        ledger.Line(advances_account, credit=adv.amount, unit=adv.unit, memo=memo),
                    ],
                    source_type="payment",
                    source_id=payment_id,
                )
            except ledger.LedgerError as exc:
                raise BillingError(str(exc)) from exc
            created_advances.append(
                Payment.objects.create(
                    id=payment_id,
                    site=site,
                    unit=adv.unit,
                    date=day,
                    amount=adv.amount,
                    channel=Payment.Channel.OPENING,
                    account=opening,
                    note=memo,
                    entry=entry,
                    created_by=user,
                )
            )
        if created_advances:
            record(
                "opening.advances",
                site,
                {"toplam": sum(a.amount for a in advances), "bolum": len(advances)},
                site_id=site.pk,
            )
            apply_advances(site, [a.unit for a in advances], day=day)
    return OpeningResult(batch, created_advances)
