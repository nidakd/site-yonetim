"""Tarihli aylık çizelge (aidat ya da yakıt): her satır bir daire, her sütun bir ay.

Hücre kuralları (kullanıcıyla netleştirildi, 24.11.2026):

Sabit aidatlı çizelge (seçeneklerde "Aylık aidat" girilir):
    tarih (hücre tarih ya da "26.05.2026")  → o ayın aidatı o tarihte ödendi
    tarih + "e" ("26.05.2026e")              → aynı, elden (nakit) ödendi; eksiz tarih = banka
    "300/28.03.2026"                         → o tarihte 300 TL ödendi (kısmi ya da toplu ödeme)
    sayı (800, 1600, 2400 …)                 → o ay ödenmedi; sayı o aya kadar biriken borçtur
    "muaf"                                    → o ay borçlandırılmaz
    boş                                       → o ay borçlandırılmaz (henüz gelmemiş ay)

Tutarlı çizelge (yakıt; "Aylık aidat" boş bırakılır):
    sayı (1841)                               → o ayın borcu 1841 TL, ödenmedi
    "1489/25.10.26"                           → o ayın borcu 1489 TL, o tarihte ödendi
    "muaf" / boş                              → borç yok

"GEÇMİŞ BORÇ" / "ESKİ BORÇ" sütunu: ilk aydan önceki devreden borç (açılış borcu).
Daire ve blok yoksa oluşturulur; AD/SOYAD varsa daireye sorumlu kişi olarak eklenir.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.billing.models import Charge, ChargeType, DuesAgreement
from apps.people.models import Occupancy
from apps.property.models import Block, Unit
from domain.cells import AMOUNT, fold, month_index, norm, parse_cell, to_kurus
from domain.money import format_try

from .xlsx import MAX_ROWS, Issues, SheetError, label, open_workbook

OPENING_HEADERS = ("gecmis borc", "eski borc", "devreden borc", "devir")
SKIP_HEADERS = ("toplam",)
_DATE = r"(\d{1,2})\s*\.+\s*(\d{1,2})\s*\.+\s*(\d{2,4})"
_DATE_ONLY = re.compile(rf"^{_DATE}\s*(e)?$")
_AMOUNT_DATE = re.compile(rf"^([\d.,]+)\s*/\s*{_DATE}\s*(e)?$")


def _add_months(day: date, n: int) -> date:
    m = day.month - 1 + n
    return date(day.year + m // 12, m % 12 + 1, 1)


def _mkdate(d: str, m: str, y: str) -> date | None:
    year = int(y) + (2000 if len(y) <= 2 else 0)
    try:
        return date(year, int(m), int(d))
    except ValueError:
        return None


def _money(text: str) -> int | None:
    """'1489', '446,5', '1276.67', '1.489' → kuruş."""
    cell = parse_cell(text)
    if cell.kind != AMOUNT:
        return None
    return to_kurus(cell.value)


@dataclass
class Cell:
    kind: str  # paid | partial | unpaid | credit | exempt | empty | unknown
    amount: int | None = None  # ödeme tutarı (partial) ya da borç tutarı (tutarlı çizelgede)
    day: date | None = None
    cash: bool = False
    raw: str = ""


def classify(value: object) -> Cell:
    if value is None or (isinstance(value, str) and not value.strip()):
        return Cell("empty")
    if isinstance(value, datetime):
        return Cell("paid", day=value.date(), raw=value.date().isoformat())
    if isinstance(value, date):
        return Cell("paid", day=value, raw=value.isoformat())
    if isinstance(value, bool):
        return Cell("unknown", raw=str(value))
    if isinstance(value, int | float | Decimal):
        try:
            kurus = to_kurus(Decimal(str(value)))
        except InvalidOperation:
            kurus = None
        return Cell("unpaid", amount=kurus, raw=str(value)) if kurus is not None else Cell("unknown", raw=str(value))
    text = " ".join(str(value).split())
    low = norm(text)
    credit = re.match(r"^(.*?)\s*art[ıi]da\.?$", low)
    if credit:  # "200 artıda", "657-30=627 artıda": fazla ödeme (avans)
        body = credit.group(1)
        amount = _money(body.rsplit("=", 1)[-1]) if body else None
        return Cell("credit", amount=amount, raw=text) if amount else Cell("unknown", raw=text)
    if low in ("muaf", "muaf.", "muafiyet"):
        return Cell("exempt", raw=text)
    if m := _DATE_ONLY.match(low):
        day = _mkdate(m[1], m[2], m[3])
        return Cell("paid", day=day, cash=bool(m[4]), raw=text) if day else Cell("unknown", raw=text)
    if m := _AMOUNT_DATE.match(low):
        day, amount = _mkdate(m[2], m[3], m[4]), _money(m[1])
        if day and amount:
            return Cell("partial", amount=amount, day=day, cash=bool(m[5]), raw=text)
        return Cell("unknown", raw=text)
    amount = _money(text)
    if amount is None and "=" in text:  # "7400-1600 = 5.800": sonuç eşittirin sağında
        amount = _money(text.rsplit("=", 1)[1])
    if amount is not None:
        return Cell("unpaid", amount=amount, raw=text)
    return Cell("unknown", raw=text)


# --- çözümleme --------------------------------------------------------------------------------


def _find_header(grid) -> int:
    for i, row in enumerate(grid[:30]):
        heads = [fold(label(v)) for v in row]
        if any(h.startswith("blok") for h in heads) and any(h.startswith("daire") for h in heads):
            return i
    raise SheetError("Başlık satırı bulunamadı: 'BLOK' ve 'DAIRE' sütunları olmalı.")


def _month_of(header: str) -> int | None:
    """'OCAK3', 'EKİM ', 'Oca.26', 'şubat2' → ay numarası."""
    word = re.match(r"[^\W\d_]+", fold(header))
    return month_index(word.group(0)) if word else None


def parse(data: bytes, options: dict) -> tuple[dict, Issues]:
    issues = Issues()
    wb = open_workbook(data)
    names = [ws.title for ws in wb.worksheets]
    wanted = (options.get("sheet_name") or "").strip()
    if wanted:
        ws = next((w for w in wb.worksheets if norm(w.title) == norm(wanted)), None)
        if ws is None:
            raise SheetError(f"'{wanted}' adlı sayfa yok. Sayfalar: {', '.join(names)}")
    elif len(names) == 1:
        ws = wb.worksheets[0]
    else:
        raise SheetError(f"Dosyada birden çok sayfa var; hangisinin aktarılacağını yazın: {', '.join(names)}")
    if not options.get("first_month"):
        raise SheetError("İlk ay sütununun ayını ve yılını seçin (ör. aidat 2026 için 2026-01).")
    first_month = date.fromisoformat(options["first_month"])

    grid = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i > MAX_ROWS:
            raise SheetError(f"Sayfada en fazla {MAX_ROWS} satır olabilir.")
        grid.append(list(row))
    h = _find_header(grid)
    header = [label(v) for v in grid[h]]
    col = {}
    month_cols: list[tuple[int, date]] = []
    opening_col = None
    for idx, text in enumerate(header):
        n = fold(text)
        if not n:
            continue
        if n.startswith("blok") and "block" not in col:
            col["block"] = idx
        elif n.startswith("daire") and "number" not in col:
            col["number"] = idx
        elif n in ("ad", "adi", "isim") and "first" not in col:
            col["first"] = idx
        elif n in ("soyad", "soyadi") and "last" not in col:
            col["last"] = idx
        elif n.startswith(OPENING_HEADERS) and opening_col is None:
            opening_col = idx
        elif n.startswith(SKIP_HEADERS):
            issues.info(f"{ws.title} başlık", f"'{text}' sütunu hesaba katılmadı.")
        elif (month := _month_of(text)) is not None:
            expected = _add_months(first_month, len(month_cols))
            if month != expected.month:
                raise SheetError(
                    f"'{text}' sütunu {expected:%m.%Y} olmalıydı; ay sütunları ardışık değil. İlk ayı kontrol edin."
                )
            month_cols.append((idx, expected))
    if not month_cols:
        raise SheetError("Ay sütunu bulunamadı (OCAK, ŞUBAT … başlıkları).")

    fixed_fee = options.get("monthly_fee")
    rows = []
    for r in range(h + 1, len(grid)):
        values = grid[r]
        excel_row = r + 1

        def get(key, values=values):
            i = col.get(key)
            return values[i] if i is not None and i < len(values) else None

        block, number = label(get("block")), label(get("number"))
        if not block and not number:
            continue
        if not number:
            issues.warning(f"{ws.title} satır {excel_row}", "Daire no boş; satır atlandı.")
            continue
        cells = []
        for idx, period in month_cols:
            cell = classify(values[idx] if idx < len(values) else None)
            coord = f"{ws.title}!{_col(idx)}{excel_row}"
            if cell.kind == "credit":
                issues.error(coord, f"'artıda' yalnızca geçmiş borç sütununda kullanılabilir: {cell.raw!r}")
            elif cell.kind == "unknown":
                issues.error(coord, f"Hücre okunamadı: {cell.raw!r}. Tarih, tutar, tutar/tarih ya da 'muaf' olmalı.")
            elif cell.day and cell.day > timezone.localdate():
                issues.error(coord, f"Ödeme tarihi ileri bir tarih: {cell.raw!r}")
            elif fixed_fee is None and cell.kind == "paid":
                issues.error(coord, f"Tutarsız ödeme: {cell.raw!r}. Bu çizelgede 'tutar/tarih' yazılmalı.")
            elif cell.kind == "partial" and fixed_fee and cell.amount > fixed_fee:
                issues.info(coord, f"{format_try(cell.amount)} ödeme birden çok ayı kapatır (en eski borçtan).")
            cells.append({"period": period.isoformat(), "coord": coord, **cell.__dict__, "day": _iso(cell.day)})
        opening = None
        if opening_col is not None:
            cell = classify(values[opening_col] if opening_col < len(values) else None)
            coord = f"{ws.title}!{_col(opening_col)}{excel_row}"
            if cell.kind in ("unpaid", "partial", "credit"):
                opening = {"coord": coord, **cell.__dict__, "day": _iso(cell.day)}
            elif cell.kind == "unknown":
                issues.error(coord, f"Geçmiş borç okunamadı: {cell.raw!r}")
            elif cell.kind == "paid":
                issues.info(coord, "Geçmiş borç sütununda tarih var; ödenmiş sayıldı, aktarılmadı.")
        rows.append(
            {
                "row": excel_row,
                "block": block,
                "number": number,
                "first": label(get("first"))[:80],
                "last": label(get("last"))[:80],
                "cells": cells,
                "opening": opening,
            }
        )
    if not rows:
        raise SheetError("Sayfada daire satırı bulunamadı.")
    return {"sheet": ws.title, "months": [p.isoformat() for _, p in month_cols], "rows": rows}, issues


def _col(idx: int) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(idx + 1)


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


# --- plan -------------------------------------------------------------------------------------


@dataclass
class LedgerRow:
    row: int
    label: str
    person: str
    unit_exists: bool
    charged: int = 0
    paid: int = 0
    opening: int = 0
    advance: int = 0
    sheet_balance: int | None = None
    exempt_months: int = 0

    @property
    def balance(self) -> int:
        return self.opening - self.advance + self.charged - self.paid

    @property
    def months_balance(self) -> int:
        """Yalnızca çizelgedeki ayların borcu (geçmiş borç hariç) — çizelgedeki birikmiş sayıyla karşılaştırılır."""
        return self.charged - self.paid


@dataclass
class LedgerPlan:
    issues: Issues
    ledger_rows: list[LedgerRow] = field(default_factory=list)
    charges: dict = field(default_factory=dict)  # period → [(key, amount)]
    payments: list = field(default_factory=list)  # [(key, day, amount, cash)]
    openings: list = field(default_factory=list)  # [(key, amount)]
    advances: list = field(default_factory=list)  # [(key, amount)] geçmişten fazla ödeme
    new_blocks: list = field(default_factory=list)
    new_units: list = field(default_factory=list)  # [(block, number, first, last)]
    exempt_from: dict = field(default_factory=dict)  # key → ilk muaf ay (sonda muaf olanlar)
    grid_rows: list = field(default_factory=list)  # şablonla uyum için

    @property
    def summary(self) -> dict:
        charged = sum(a for items in self.charges.values() for _, a in items)
        paid = sum(p[2] for p in self.payments)
        opening = sum(a for _, a in self.openings)
        return {
            "daire": len(self.ledger_rows),
            "yeni_blok": len(self.new_blocks),
            "yeni_bolum": len(self.new_units),
            "ay": len(self.charges),
            "borc_kalemi": sum(len(v) for v in self.charges.values()),
            "borclandirilan": charged,
            "tahsilat_adedi": len(self.payments),
            "tahsilat_toplam": paid,
            "acilis_borc_toplam": opening,
            "acilis_borc_kalemi": len(self.openings),
            "acilis_avans_toplam": sum(a for _, a in self.advances),
            "acilis_avans_bolum": len(self.advances),
            "kalan": opening - sum(a for _, a in self.advances) + charged - paid,
            "muaf_daire": len(self.exempt_from),
        }


def unit_key(block: str, number: str) -> tuple[str, str]:
    return norm(block), norm(number)


def plan(site, payload: dict, options: dict) -> LedgerPlan:
    from apps.ledger import services as ledger

    issues = Issues()
    p = LedgerPlan(issues)
    fee = options.get("monthly_fee")
    charge_type = ChargeType.objects.filter(site=site, name=options.get("charge_type") or "Aidat").first()
    if charge_type is None:
        issues.error("Seçenekler", f"'{options.get('charge_type')}' borç türü bu sitede yok.")
        return p
    months = [date.fromisoformat(m) for m in payload["months"]]
    blocks = {norm(b.name): b for b in Block.objects.filter(site=site)}
    units = {
        unit_key(u.block.name if u.block_id else "", u.number): u
        for u in Unit.objects.filter(site=site).select_related("block")
    }
    seen = {}
    for r in payload["rows"]:
        where = f"{payload['sheet']} satır {r['row']}"
        key = unit_key(r["block"], r["number"])
        if key in seen:
            issues.error(where, f"Bu daire çizelgede iki kez var (satır {seen[key]}).")
            continue
        seen[key] = r["row"]
        if key not in units:
            if r["block"] and norm(r["block"]) not in blocks and r["block"] not in p.new_blocks:
                p.new_blocks.append(r["block"])
            p.new_units.append((r["block"], r["number"], r["first"], r["last"]))
        person = f"{r['first']} {r['last']}".strip()
        lr = LedgerRow(r["row"], f"{r['block']} · {r['number']}" if r["block"] else r["number"], person, key in units)
        if r["opening"]:
            o = r["opening"]
            amount = o["amount"] or 0
            if o["kind"] == "credit":
                p.advances.append((key, amount))
                lr.advance = amount
            elif amount:
                p.openings.append((key, amount))
                lr.opening = amount
            if o["kind"] == "partial":  # "tutar/tarih": borç ödendi
                p.payments.append((key, date.fromisoformat(o["day"]), amount, o["cash"]))
                lr.paid += amount
        last_nonempty = None
        exempt_run_start = None
        for c in r["cells"]:
            period = date.fromisoformat(c["period"])
            kind = c["kind"]
            if kind in ("empty", "unknown"):
                continue
            last_nonempty = c
            if kind == "exempt":
                lr.exempt_months += 1
                exempt_run_start = exempt_run_start or period
                continue
            exempt_run_start = None
            if fee:  # sabit aidat
                amount = fee
                pay = fee if kind == "paid" else (c["amount"] if kind == "partial" else 0)
            else:  # tutarlı çizelge
                amount = c["amount"] or 0
                pay = amount if kind == "partial" else 0
            if amount:
                p.charges.setdefault(period, []).append((key, amount))
                lr.charged += amount
            if pay:
                p.payments.append((key, date.fromisoformat(c["day"]), pay, c["cash"]))
                lr.paid += pay
        if last_nonempty and last_nonempty["kind"] == "exempt" and exempt_run_start and charge_type.apply_agreements:
            p.exempt_from[key] = exempt_run_start
        if fee and last_nonempty and last_nonempty["kind"] == "unpaid":
            lr.sheet_balance = last_nonempty["amount"]
            if lr.sheet_balance not in (lr.months_balance, lr.balance):
                issues.warning(
                    last_nonempty["coord"],
                    f"Çizelgedeki birikmiş borç {format_try(lr.sheet_balance or 0)}, hesaplanan "
                    f"{format_try(lr.months_balance)} (geçmiş borç hariç). Önizlemede kontrol edin.",
                )
        p.ledger_rows.append(lr)

    # aynı ay aynı türden zaten borçlandırılmış daireler
    existing = {
        (unit_key(c.unit.block.name if c.unit.block_id else "", c.unit.number), c.period)
        for c in Charge.objects.filter(
            site=site, charge_type=charge_type, period__in=months, is_cancelled=False
        ).select_related("unit__block")
    }
    if charge_type.once_per_period:
        clash = sorted(
            {
                f"{k[0]} {k[1]} ({per:%m.%Y})"
                for per, items in p.charges.items()
                for k, _ in items
                if (k, per) in existing
            }
        )
        if clash:
            issues.error("Sistem", f"Bu aylar zaten borçlandırılmış ({len(clash)} kalem): {', '.join(clash[:8])}")
    until = ledger.locked_until(site)
    if until and months and months[0] - timedelta(days=1) <= until:
        issues.error("Seçenekler", f"Çizelgenin ayları kilitli dönemde ({until:%d.%m.%Y} ve öncesi kilitli).")
    both = {k for k, _ in p.openings} & {k for k, _ in p.advances}
    if both:
        issues.error("Çizelge", "Aynı dairede hem geçmiş borç hem 'artıda' var.")
    if not p.charges and not p.payments and not p.openings and not p.advances and not issues.has_errors:
        issues.warning("Çizelge", "Aktarılacak borç ya da ödeme yok.")
    return p


# --- uygulama ---------------------------------------------------------------------------------


def apply(site, payload: dict, options: dict, user):
    from apps.billing import history, opening
    from apps.billing.services import BillingError
    from apps.ledger import services as ledger
    from apps.payments import services as payments
    from apps.people.services import find_or_create_person

    from .setup import ApplyError

    with transaction.atomic():
        p = plan(site, payload, options)
        if p.issues.has_errors:
            raise ApplyError("Çizelgede düzeltilmesi gereken hatalar var; önizlemeyi yenileyin.")
        charge_type = ChargeType.objects.get(site=site, name=options.get("charge_type") or "Aidat")
        months = [date.fromisoformat(m) for m in payload["months"]]
        start = months[0]
        # yapı ve kişiler
        blocks = {norm(b.name): b for b in Block.objects.filter(site=site)}
        order = max([b.order for b in blocks.values()], default=0) + 1
        for name in p.new_blocks:
            blocks[norm(name)] = Block.objects.create(site=site, name=name, order=order)
            order += 1
        for block_name, number, first, last in p.new_units:
            unit = Unit.objects.create(
                site=site, block=blocks.get(norm(block_name)) if block_name else None, number=number
            )
            if options.get("create_people", True) and first:
                person, _ = find_or_create_person(first, last or "-")
                Occupancy.objects.create(unit=unit, person=person, relation="owner", start_date=start, is_liable=True)
        units = {
            unit_key(u.block.name if u.block_id else "", u.number): u
            for u in Unit.objects.filter(site=site).select_related("block")
        }
        try:
            if p.openings or p.advances:
                opening.post_opening(
                    site,
                    day=start - timedelta(days=1),
                    debts=[
                        opening.OpeningDebt(units[k], _add_months(start, -1), a, "Geçmiş borç") for k, a in p.openings
                    ],
                    advances=[opening.OpeningAdvance(units[k], a) for k, a in p.advances],
                    user=user,
                )
            for period in sorted(p.charges):
                last_day = calendar.monthrange(period.year, period.month)[1]
                history.post_month(
                    site,
                    charge_type,
                    period,
                    period.replace(day=min(10, last_day)),
                    [(units[k], a) for k, a in p.charges[period]],
                    user,
                    f"{_month_name(period)} {period.year} {charge_type.name.lower()} (Excel)",
                )
            cash = history.transfer_account(site)
            for key, day, amount, is_cash in sorted(p.payments, key=lambda x: (x[1], x[0])):
                payments.create_payment(
                    site,
                    units[key],
                    amount=amount,
                    day=day,
                    channel="cash" if is_cash else "transfer",
                    account=cash,
                    user=user,
                    note=f"Excel aktarımı ({payload['sheet']})",
                )
            for key, valid_from in p.exempt_from.items():
                unit = units[key]
                if not DuesAgreement.objects.filter(unit=unit, kind="exempt", valid_to__isnull=True).exists():
                    DuesAgreement.objects.create(
                        unit=unit, kind="exempt", valid_from=valid_from, decision_ref="Excel aktarımı: çizelgede muaf"
                    )
        except (BillingError, payments.PaymentError, ledger.LedgerError) as exc:
            raise ApplyError(str(exc)) from exc
    return p


def _month_name(day: date) -> str:
    from apps.core.templatetags.ui import MONTHS

    return MONTHS[day.month - 1]
