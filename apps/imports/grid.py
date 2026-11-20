"""Aylık aidat çizelgesi (eski `aidat_takip` biçimi): her satır bir bölüm, her sütun bir ay,
hücrelerde o ay ödenen tutar. Çizelgeden açılış bakiyesi çıkarılır:

    borç (ay başına)  = bölüm tipinin aidatı (ya da girilen aylık aidat), özel aidat anlaşması uygulanmış
    ödenen            = satırdaki tüm hücrelerin toplamı (iade eksi)
    yaşlandırma       = ödemeler en eski aydan başlayarak kapatır; kalan aylar açılış borcu olur
    fazla ödeme       = açılış avansı

Bölümler sistemde önceden kayıtlı olmalı (kurulum şablonuyla ya da elle). Çizelgedeki isimler
okunmaz; kişiler kurulum şablonundan gelir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from apps.billing.services import agreements_on, to_domain
from apps.property.models import Unit
from domain.cells import (
    AMOUNT,
    EMPTY,
    F_PARTIAL,
    FLAG_TEXT,
    NOTE,
    month_index,
    norm,
    owed_by_month,
    parse_cell,
    parse_month,
    to_kurus,
)
from domain.distribution import apply_agreement
from domain.money import format_try

from . import setup
from .xlsx import MAX_ROWS, Issues, SheetError, label, open_workbook

DEFAULT_UNIT_COLUMN = "Daire No"


def _add_months(day: date, n: int) -> date:
    m = day.month - 1 + n
    return date(day.year + m // 12, m % 12 + 1, 1)


def _month_of_header(value: object) -> tuple[date | None, int | None]:
    """(tam dönem, yalnızca ay numarası). 'Eylül 2026' → (2026-09-01, 9); 'Eylül' → (None, 9)."""
    full = parse_month(value)
    if full:
        return full, full.month
    text = label(value)
    if not text or re.search(r"\d", text):
        return None, None
    return None, month_index(text.split()[0])


def parse(data: bytes, options: dict) -> tuple[dict, Issues]:
    issues = Issues()
    wb = open_workbook(data)
    ws = wb.worksheets[0]
    grid = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i > MAX_ROWS:
            raise SheetError(f"Sayfada en fazla {MAX_ROWS} satır olabilir.")
        grid.append(list(row))

    unit_title = norm(options.get("unit_column") or DEFAULT_UNIT_COLUMN)
    header_no = next(
        (r for r in range(min(len(grid), 30)) if unit_title in [norm(label(v)) for v in grid[r]]),
        None,
    )
    if header_no is None:
        raise SheetError(
            f"'{options.get('unit_column') or DEFAULT_UNIT_COLUMN}' başlıklı sütun ilk 30 satırda bulunamadı "
            f"(ilk sayfa: {ws.title}). Bölüm numarası sütununun başlığını seçeneklerde yazın."
        )
    header = grid[header_no]
    unit_col = [norm(label(v)) for v in header].index(unit_title)

    first_month = date.fromisoformat(options["first_month"]) if options.get("first_month") else None
    month_cols: list[tuple[int, date]] = []
    expected = None
    for idx, value in enumerate(header):
        full, month = _month_of_header(value)
        if month is None:
            continue
        if full is None:
            if expected is None:
                if first_month is None:
                    raise SheetError("Ay başlıklarında yıl yok; 'İlk ay' seçeneğini girin (ör. 2026-01).")
                if first_month.month != month:
                    raise SheetError(
                        f"İlk ay sütunu {label(value)!r}, seçenekteki ilk ay {first_month:%m.%Y}; ikisi aynı ay olmalı."
                    )
                full = first_month
            else:
                full = expected
                if full.month != month:
                    raise SheetError(f"Ay sütunları ardışık değil: {label(value)!r} beklenmiyordu.")
        if month_cols and full != _add_months(month_cols[-1][1], 1):
            raise SheetError(f"Ay sütunları ardışık değil: {label(value)!r}.")
        month_cols.append((idx, full))
        expected = _add_months(full, 1)
    if not month_cols:
        raise SheetError("Ay sütunu bulunamadı (başlıklar Ocak, Şubat... ya da 2026-01 gibi olmalı).")

    rows = []
    for number in range(header_no + 1, len(grid)):
        values = grid[number]
        excel_row = number + 1
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in values):
            continue
        unit_label = label(values[unit_col] if unit_col < len(values) else None)
        if norm(unit_label).startswith(("toplam", "genel toplam")):
            issues.info(f"satır {excel_row}", "Toplam satırı hesaba katılmadı.")
            continue
        if not unit_label:
            issues.warning(f"satır {excel_row}", "Satırda veri var ama bölüm numarası boş; atlandı.")
            continue
        paid_total = 0
        for idx, _period in month_cols:
            coord = f"{ws.title}!{_col(idx)}{excel_row}"
            cell = parse_cell(values[idx] if idx < len(values) else None)
            if cell.kind == EMPTY:
                continue
            if cell.kind == NOTE:
                text = FLAG_TEXT.get(cell.flags[0], "") if cell.flags else ""
                issues.warning(coord, f"Tutar okunamadı, 0 sayıldı: {cell.raw!r} {text}".strip())
                continue
            kurus = to_kurus(cell.value) if cell.kind == AMOUNT else None
            if kurus is None:
                issues.error(coord, f"Tutarda en fazla iki kuruş hanesi olabilir: {cell.raw!r}")
                continue
            for flag in cell.flags:
                (issues.info if flag == F_PARTIAL else issues.warning)(coord, f"{FLAG_TEXT[flag]}: {cell.raw!r}")
            paid_total += kurus
        rows.append({"row": excel_row, "label": unit_label, "paid": paid_total})
    if not rows:
        raise SheetError("Çizelgede bölüm satırı bulunamadı.")
    return {"months": [p.isoformat() for _, p in month_cols], "rows": rows, "sheet": ws.title}, issues


def _col(idx: int) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(idx + 1)


def compact(text: str) -> str:
    """'A Blok · 5', 'A-5', 'a5', 'A blok no:5' → 'a5'."""
    s = norm(text).replace("blok", " ").replace("no", " ")
    return re.sub(r"[\s\-./·:_#]", "", s)


@dataclass
class GridRow:
    row: int
    label: str
    unit: Unit | None
    monthly: int
    expected: int
    paid: int
    owed: int
    advance: int


def match_units(site) -> tuple[dict[str, list[Unit]], dict[str, list[Unit]]]:
    full, by_number = {}, {}
    for unit in Unit.objects.filter(site=site, is_archived=False).select_related("block", "unit_type"):
        block = unit.block.name if unit.block_id else ""
        full.setdefault(compact(f"{block}{unit.number}"), []).append(unit)
        by_number.setdefault(compact(unit.number), []).append(unit)
    return full, by_number


def plan(site, payload: dict, options: dict) -> setup.Plan:
    issues = Issues()
    p = setup.Plan(issues)
    months = [date.fromisoformat(m) for m in payload["months"]]
    fallback_fee = options.get("monthly_fee")
    full, by_number = match_units(site)
    used: dict[int, int] = {}
    sheet = payload.get("sheet", "Çizelge")
    opening_day = date.fromisoformat(options["opening_date"]) if options.get("opening_date") else None
    if opening_day and months[-1] > opening_day:
        issues.error("Seçenekler", "Açılış tarihi, çizelgedeki son aydan önce olamaz.")

    for row in payload["rows"]:
        where = f"{sheet} satır {row['row']}"
        key = compact(row["label"])
        candidates = full.get(key) or by_number.get(key) or []
        if len(candidates) != 1:
            reason = "birden çok bölümle eşleşti" if candidates else "sistemde bulunamadı"
            issues.error(where, f"'{row['label']}' {reason}. Bölüm numarasını 'A Blok 5' gibi yazın.")
            p.grid_rows.append(GridRow(row["row"], row["label"], None, 0, 0, row["paid"], 0, 0))
            continue
        unit = candidates[0]
        if unit.pk in used:
            issues.error(where, f"'{unit.label}' çizelgede iki kez var (satır {used[unit.pk]}).")
            continue
        used[unit.pk] = row["row"]
        base = unit.unit_type.default_amount if unit.unit_type_id and unit.unit_type.default_amount else fallback_fee
        if not base:
            issues.error(where, f"{unit.label}: bölüm tipinin aidatı yok; seçeneklerde aylık aidatı girin.")
            continue
        fees = []
        for period in months:
            agreement = agreements_on([unit], period).get(unit.pk)
            fees.append(apply_agreement(base, to_domain(agreement)))
        remaining, advance = owed_by_month(fees, row["paid"])
        owed = sum(remaining)
        block = unit.block.name if unit.block_id else ""
        for period, amount in zip(months, remaining, strict=True):
            if amount:
                p.debts.append(
                    {
                        "key": setup.unit_key(block, unit.number),
                        "period": period.isoformat(),
                        "amount": amount,
                        "description": f"Devreden aidat {period:%m.%Y}",
                    }
                )
        if advance:
            p.advances.append({"key": setup.unit_key(block, unit.number), "amount": advance})
        p.grid_rows.append(GridRow(row["row"], row["label"], unit, base, sum(fees), row["paid"], owed, advance))
        if fees and fees[0] != base:
            issues.info(where, f"{unit.label}: özel aidat anlaşması uygulandı ({format_try(fees[0])}/ay).")

    missing = [u.label for units in by_number.values() for u in units if u.pk not in used]
    if missing:
        issues.info("Çizelge", f"Çizelgede olmayan {len(missing)} bölüm: {', '.join(missing[:10])}")
    if opening_day is None:
        issues.error("Seçenekler", "Açılış tarihini girin.")
    else:
        from apps.ledger import services as ledger

        until = ledger.locked_until(site)
        if until and opening_day <= until:
            issues.error("Seçenekler", f"Açılış tarihi kilitli dönemde ({until:%d.%m.%Y} ve öncesi kilitli).")
    if not p.debts and not p.advances and not issues.has_errors:
        issues.warning("Çizelge", "Açılış bakiyesi çıkmadı: tüm bölümlerin ödemesi tam.")
    return p


def apply(site, payload: dict, options: dict, user) -> setup.Plan:
    from django.db import transaction

    with transaction.atomic():
        p = plan(site, payload, options)
        if p.issues.has_errors:
            raise setup.ApplyError("Çizelgede düzeltilmesi gereken hatalar var; önizlemeyi yenileyin.")
        setup.post_openings(site, p, options, user)
    return p
