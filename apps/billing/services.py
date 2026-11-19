"""Tahakkuk: borç türleri, özel aidat anlaşmaları, toplu borçlandırma (önizleme → onay → iptal)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.audit.services import record
from apps.ledger import services as ledger
from apps.ledger.models import Account
from apps.people.models import Occupancy, active_on
from apps.property.models import Unit
from apps.tenancy.models import Site
from domain.distribution import Agreement, AgreementKind, DistributionError, distribute_total, per_unit

from .models import Charge, ChargeBatch, ChargeType, DuesAgreement


class BillingError(ValueError):
    pass


# (ad, gelir hesabı kodu, öncelik, özel aidat uygulanır, dönemde bir kez)
DEFAULT_CHARGE_TYPES = [
    ("Aidat", "600", 10, True, True),
    ("Yakıt", "601", 20, False, True),
    ("Özel toplama", "602", 30, False, False),
    ("Diğer", "649", 40, False, False),
]


def setup_site(site: Site) -> None:
    """Yeni siteye hesap planı ve borç türlerini kurar (idempotent)."""
    ledger.ensure_chart(site)
    if ChargeType.objects.filter(site=site).exists():
        return
    accounts = {a.code: a for a in Account.objects.filter(site=site)}
    for name, code, priority, agreements, once in DEFAULT_CHARGE_TYPES:
        ChargeType.objects.create(
            site=site,
            name=name,
            income_account=accounts[code],
            priority=priority,
            apply_agreements=agreements,
            once_per_period=once,
        )


# --- özel aidat anlaşmaları -------------------------------------------------------------------


def agreements_on(units, day: date) -> dict[int, DuesAgreement]:
    qs = DuesAgreement.objects.filter(unit__in=units, valid_from__lte=day).filter(
        Q(valid_to__isnull=True) | Q(valid_to__gte=day)
    )
    return {a.unit_id: a for a in qs.order_by("valid_from", "id")}  # aynı günde birden çoksa sonuncusu


def overlapping_agreements(unit, start: date, end: date | None, exclude_pk=None):
    qs = DuesAgreement.objects.filter(unit=unit).exclude(pk=exclude_pk)
    qs = qs.filter(Q(valid_to__isnull=True) | Q(valid_to__gte=start))
    if end is not None:
        qs = qs.filter(valid_from__lte=end)
    return qs


def to_domain(agreement: DuesAgreement | None) -> Agreement | None:
    if agreement is None:
        return None
    kind = AgreementKind(agreement.kind)
    if kind is AgreementKind.FIXED:
        return Agreement(kind, amount=agreement.amount or 0)
    if kind is AgreementKind.DISCOUNT:
        return Agreement(kind, percent=Fraction(agreement.discount_percent))
    return Agreement(kind)


def end_agreement(agreement: DuesAgreement, end: date) -> None:
    if end < agreement.valid_from:
        raise BillingError("Bitiş tarihi başlangıçtan önce olamaz.")
    with transaction.atomic():
        agreement.valid_to = end
        agreement.save(update_fields=["valid_to", "updated_at"])
        record("agreement.ended", agreement, {"bitis": str(end)}, site_id=agreement.unit.site_id)


# --- sorumlu kişiler --------------------------------------------------------------------------


def responsible_on(units, day: date) -> dict[int, list[dict]]:
    """Dönem başında borç sorumlusu olan kişiler (malik/kiracı), bölüm başına."""
    out: dict[int, list[dict]] = {}
    qs = (
        Occupancy.objects.filter(unit__in=units, is_liable=True)
        .filter(active_on(day))
        .select_related("person")
        .order_by("relation", "start_date", "id")
    )
    for occ in qs:
        out.setdefault(occ.unit_id, []).append(
            {"person_id": occ.person_id, "name": occ.person.full_name, "relation": occ.get_relation_display()}
        )
    return out


# --- toplu borçlandırma -----------------------------------------------------------------------


@dataclass
class PreviewRow:
    unit: Unit
    base: int = 0
    amount: int = 0
    agreement: DuesAgreement | None = None
    responsible: list[dict] = field(default_factory=list)
    skipped: str = ""  # ör. "bu dönem zaten borçlandırılmış"


@dataclass
class Preview:
    rows: list[PreviewRow]
    errors: list[str]

    @property
    def total(self) -> int:
        return sum(r.amount for r in self.rows if not r.skipped)

    @property
    def charged_rows(self) -> list[PreviewRow]:
        return [r for r in self.rows if not r.skipped and r.amount > 0]


def target_units(batch: ChargeBatch):
    units = Unit.objects.filter(site=batch.site, is_archived=False).select_related("block", "unit_type")
    if batch.unit_id:
        units = units.filter(pk=batch.unit_id)
    elif batch.block_id:
        units = units.filter(block_id=batch.block_id)
    return list(units)


def compute_preview(batch: ChargeBatch) -> Preview:
    units = target_units(batch)
    errors: list[str] = []
    if not units:
        return Preview([], ["Kapsamda borçlandırılacak aktif bölüm yok."])

    already: set[int] = set()
    if batch.charge_type.once_per_period:
        already = set(
            Charge.objects.filter(
                unit__in=units, charge_type=batch.charge_type, period=batch.period, is_cancelled=False
            ).values_list("unit_id", flat=True)
        )
    eligible = [u for u in units if u.pk not in already]
    agreements = agreements_on(eligible, batch.period) if batch.charge_type.apply_agreements else {}
    domain_agreements = {uid: to_domain(a) for uid, a in agreements.items()}

    shares = {}
    method = batch.method
    try:
        if method == ChargeBatch.Method.PER_UNIT:
            shares = per_unit({u.pk: batch.amount for u in eligible}, domain_agreements)
        elif method == ChargeBatch.Method.UNIT_TYPE:
            missing = [u.label for u in eligible if not u.unit_type or u.unit_type.default_amount is None]
            if missing:
                errors.append(f"Bölüm tipi ya da tipin aidatı girilmemiş: {', '.join(missing[:15])}")
            else:
                shares = per_unit({u.pk: u.unit_type.default_amount for u in eligible}, domain_agreements)
        else:
            weights, missing = {}, []
            for u in eligible:
                w = {ChargeBatch.Method.EQUAL: 1, ChargeBatch.Method.SHARE: u.share}.get(method)
                if method == ChargeBatch.Method.AREA:
                    w = Fraction(u.gross_area) if u.gross_area else None
                if w is None:
                    missing.append(u.label)
                weights[u.pk] = w
            if missing:
                label = "Arsa payı" if method == ChargeBatch.Method.SHARE else "Brüt m²"
                errors.append(f"{label} girilmemiş bölümler: {', '.join(missing[:15])}")
            elif eligible:
                shares = distribute_total(batch.amount, weights, domain_agreements)
    except DistributionError as exc:
        errors.append(str(exc))

    responsible = responsible_on(units, batch.period)
    rows = []
    for u in units:
        row = PreviewRow(unit=u, responsible=responsible.get(u.pk, []), agreement=agreements.get(u.pk))
        if u.pk in already:
            row.skipped = "Bu dönem bu türden zaten borçlandırılmış"
        elif u.pk in shares:
            row.base, row.amount = shares[u.pk].base, shares[u.pk].amount
        rows.append(row)
    if not errors and not any(r.amount > 0 for r in rows if not r.skipped):
        errors.append("Borçlandırılacak tutar yok (tüm bölümler muaf ya da zaten borçlandırılmış).")
    return Preview(rows, errors)


def approve(batch: ChargeBatch, user) -> ChargeBatch:
    with transaction.atomic():
        Site.objects.select_for_update().get(pk=batch.site_id)  # aynı sitede eşzamanlı onayları sıraya koy
        batch = ChargeBatch.objects.select_for_update().select_related("charge_type", "site").get(pk=batch.pk)
        if batch.status != ChargeBatch.Status.DRAFT:
            raise BillingError("Yalnızca taslak tahakkuk onaylanabilir.")
        preview = compute_preview(batch)
        if preview.errors:
            raise BillingError(" ".join(preview.errors))
        rows = preview.charged_rows
        receivables = ledger.system_account(batch.site, Account.Role.RECEIVABLES)
        lines = [ledger.Line(receivables, debit=r.amount, unit=r.unit, memo=batch.description) for r in rows]
        lines.append(ledger.Line(batch.charge_type.income_account, credit=preview.total, memo=batch.description))
        try:
            entry = ledger.post(
                batch.site, batch.period, batch.description, lines, source_type="charge_batch", source_id=batch.pk
            )
        except ledger.LedgerError as exc:
            raise BillingError(str(exc)) from exc
        Charge.objects.bulk_create(
            [
                Charge(
                    organization_id=batch.organization_id,
                    site=batch.site,
                    unit=r.unit,
                    charge_type=batch.charge_type,
                    batch=batch,
                    period=batch.period,
                    due_date=batch.due_date,
                    amount=r.amount,
                    base_amount=r.base,
                    agreement=r.agreement,
                    description=batch.description,
                    responsible=r.responsible,
                    period_unique=batch.charge_type.once_per_period,
                )
                for r in rows
            ]
        )
        batch.status = ChargeBatch.Status.APPROVED
        batch.entry = entry
        batch.total, batch.unit_count = preview.total, len(rows)
        batch.approved_at, batch.approved_by = timezone.now(), user
        batch.save()
        record(
            "batch.approved",
            batch,
            {"donem": str(batch.period), "toplam": batch.total, "bolum": batch.unit_count},
            site_id=batch.site_id,
        )
        from apps.payments.services import apply_advances

        apply_advances(batch.site, [r.unit for r in rows], day=batch.period)
    return batch


def cancel(batch: ChargeBatch, user, reason: str, day: date | None = None) -> ChargeBatch:
    """Onaylı tahakkuku iptal eder: tahsilat eşleşmeleri avansa döner, tahakkuk kaydı ters çevrilir."""
    from apps.payments.models import Allocation
    from apps.payments.services import apply_advances

    day = day or timezone.localdate()
    with transaction.atomic():
        batch = ChargeBatch.objects.select_for_update().select_related("site").get(pk=batch.pk)
        if batch.status != ChargeBatch.Status.APPROVED:
            raise BillingError("Yalnızca onaylı tahakkuk iptal edilebilir.")
        site = batch.site
        receivables = ledger.system_account(site, Account.Role.RECEIVABLES)
        advances = ledger.system_account(site, Account.Role.ADVANCES)
        allocations = Allocation.objects.filter(charge__batch=batch, is_active=True).select_related("charge__unit")
        per_unit_paid: dict[int, tuple[Unit, int]] = {}
        for alloc in allocations:
            unit, total = per_unit_paid.get(alloc.charge.unit_id, (alloc.charge.unit, 0))
            per_unit_paid[unit.pk] = (unit, total + alloc.amount)
        try:
            if per_unit_paid:
                lines = []
                for unit, amount in per_unit_paid.values():
                    lines += [
                        ledger.Line(receivables, debit=amount, unit=unit, memo="Tahakkuk iptali: ödeme avansa döndü"),
                        ledger.Line(advances, credit=amount, unit=unit, memo="Tahakkuk iptali: ödeme avansa döndü"),
                    ]
                ledger.post(
                    site,
                    day,
                    f"Tahakkuk iptali, ödemeler avansa: {batch.description}",
                    lines,
                    source_type="charge_batch",
                    source_id=batch.pk,
                )
            ledger.reverse(batch.entry, day, f"İptal: {batch.description}")
        except ledger.LedgerError as exc:
            raise BillingError(str(exc)) from exc
        allocations.update(is_active=False, reversed_at=timezone.now())
        batch.charges.update(is_cancelled=True)
        batch.status = ChargeBatch.Status.CANCELLED
        batch.cancelled_at, batch.cancelled_by, batch.cancel_reason = timezone.now(), user, reason
        batch.save()
        record(
            "batch.cancelled",
            batch,
            {"neden": reason, "avansa_donen": sum(a for _, a in per_unit_paid.values())},
            site_id=site.pk,
        )
        if per_unit_paid:
            apply_advances(site, [u for u, _ in per_unit_paid.values()], day=day)
    return batch


# --- sorgular ---------------------------------------------------------------------------------


def open_charges(unit=None, *, site=None):
    """İptal edilmemiş ve kalanı olan borçlar; `remaining` ve `paid` alanlarıyla."""
    qs = Charge.objects.filter(is_cancelled=False)
    if unit is not None:
        qs = qs.filter(unit=unit)
    if site is not None:
        qs = qs.filter(site=site)
    qs = qs.annotate(paid=Sum("allocations__amount", filter=Q(allocations__is_active=True)))
    rows = []
    for c in qs.select_related("charge_type", "unit", "unit__block"):
        c.paid = c.paid or 0
        c.remaining = c.amount - c.paid
        if c.remaining > 0:
            rows.append(c)
    return rows
