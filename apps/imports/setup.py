"""Kurulum şablonu: tek Excel dosyasından bloklar, bölüm tipleri, bölümler, malik/kiracılar ve
açılış bakiyeleri.

Akış: `parse` (yalnızca dosya; veritabanına bakmaz) → `plan` (veritabanıyla karşılaştırır, hiçbir
şey yazmaz) → `apply` (planı tek transaction içinde uygular). Önizleme ile uygulama arasında veri
değişmişse `apply` planı yeniden kurar; hata çıkarsa hiçbir şey yazılmaz.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from apps.billing import opening
from apps.ledger import services as ledger
from apps.people.models import Occupancy, Person
from apps.property.models import Block, Unit, UnitType
from domain.cells import AMOUNT, EMPTY, FLAG_TEXT, norm, parse_cell, parse_month, to_kurus
from domain.money import format_try
from domain.phone import is_valid_phone, normalize_phone

from .xlsx import Issues, Row, SheetError, find_sheet, open_workbook, read_table, safe_text

UNITS_SHEET = ("Bölümler", "Bolumler")
OPENING_SHEET = ("Açılış bakiyeleri", "Acilis bakiyeleri", "Açılış")

# anahtar → (şablondaki başlık, kabul edilen diğer yazımlar...)
UNIT_COLUMNS: dict[str, tuple[str, ...]] = {
    "block": ("Blok", "Blok adı"),
    "number": ("Bölüm No", "Daire No", "No", "Bölüm", "Daire"),
    "floor": ("Kat",),
    "kind": ("Tür", "Bölüm türü"),
    "type": ("Bölüm tipi", "Tip"),
    "type_amount": ("Tip aidatı", "Aidat", "Aylık aidat"),
    "area": ("Brüt m²", "Brüt m2", "m²", "m2", "Alan"),
    "share_num": ("Arsa payı", "Arsa payı (pay)"),
    "share_den": ("Arsa payı paydası", "Arsa payı (payda)", "Payda"),
    "owner_first": ("Malik adı", "Malik ad"),
    "owner_last": ("Malik soyadı", "Malik soyad"),
    "owner_phone": ("Malik telefonu", "Malik telefon"),
    "owner_email": ("Malik e-postası", "Malik e-posta"),
    "tenant_first": ("Kiracı adı", "Kiracı ad"),
    "tenant_last": ("Kiracı soyadı", "Kiracı soyad"),
    "tenant_phone": ("Kiracı telefonu", "Kiracı telefon"),
    "tenant_email": ("Kiracı e-postası", "Kiracı e-posta"),
    "liable": ("Borç sorumlusu", "Sorumlu"),
}
OPENING_COLUMNS: dict[str, tuple[str, ...]] = {
    "block": ("Blok",),
    "number": ("Bölüm No", "Daire No", "No"),
    "period": ("Dönem", "Ay"),
    "amount": ("Tutar", "Bakiye"),
    "description": ("Açıklama",),
}
KINDS = {norm(label): value for value, label in Unit.Kind.choices} | {"dukkan": "shop", "işyeri": "shop"}

HELP = [
    ("Bölümler sayfası", "Her satır bir bağımsız bölüm. Yalnızca 'Bölüm No' zorunlu."),
    ("Blok", "Boş bırakılırsa bölüm bloksuz açılır. Yeni blok adları otomatik oluşturulur."),
    ("Tür", "Daire, Dükkan, Ofis, Depo ya da Otopark. Boşsa Daire."),
    ("Bölüm tipi / Tip aidatı", "Ör. 2+1 ve 1.250,00. Tipe göre aidat dağıtımında kullanılır."),
    ("Arsa payı", "Pay ve payda ayrı sütunlarda (ör. 50 ve 1000)."),
    ("Malik / kiracı", "Ad ve soyad birlikte girilir. Telefon 05xx xxx xx xx; sakin girişi bu numarayla yapılır."),
    ("Borç sorumlusu", "Malik ya da Kiracı. Boşsa kiracı varsa kiracı, yoksa malik."),
    ("Açılış bakiyeleri sayfası", "Sisteme geçmeden önceki borçlar. Her satır bir bölümün bir aylık borcu."),
    ("Dönem", "Borcun ait olduğu ay: 2026-09, 09.2026 ya da Eylül 2026."),
    ("Tutar", "Pozitif: borç. Eksi: fazla ödeme (avans); avansta dönem boş bırakılabilir."),
    ("Kişisel veri", "Bu dosya kişisel veri içerir; işiniz bitince güvenli biçimde silin."),
]


# --- şablon -----------------------------------------------------------------------------------


def build_template() -> bytes:
    """İndirilebilir boş şablon (sentetik örnek satırlarla)."""
    wb = Workbook()
    head_font, head_fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F4E79")

    def sheet(ws, columns, examples, widths=18):
        ws.append([names[0] for names in columns.values()])
        for cell in ws[1]:
            cell.font, cell.fill = head_font, head_fill
        for row in examples:
            ws.append([safe_text(v) for v in row])
        for i in range(1, len(columns) + 1):
            ws.column_dimensions[get_column_letter(i)].width = widths
        ws.freeze_panes = "A2"

    ws = wb.active
    ws.title = UNITS_SHEET[0]
    sheet(
        ws,
        UNIT_COLUMNS,
        [
            [
                "A Blok",
                "1",
                0,
                "Daire",
                "2+1",
                "1.250,00",
                95,
                50,
                1000,
                "Deniz",
                "Örnek",
                "0555 000 00 01",
                "",
                "",
                "",
                "",
                "",
                "Malik",
            ],
            [
                "A Blok",
                "2",
                0,
                "Daire",
                "3+1",
                "1.600,00",
                135,
                75,
                1000,
                "Ekin",
                "Deneme",
                "0555 000 00 02",
                "",
                "Yaz",
                "Kiracıoğlu",
                "0555 000 00 03",
                "",
                "Kiracı",
            ],
            ["", "Dükkan 1", "", "Dükkan", "", "", 60, 40, 1000, "Örnek", "Ticaret Ltd.", "", "", "", "", "", "", ""],
        ],
    )
    ws2 = wb.create_sheet(OPENING_SHEET[0])
    sheet(
        ws2,
        OPENING_COLUMNS,
        [["A Blok", "1", "2026-09", "1.250,00", "Eylül aidatı"], ["A Blok", "2", "", -500, "Fazla ödeme"]],
    )
    ws3 = wb.create_sheet("Açıklamalar")
    ws3.append(["Konu", "Açıklama"])
    for cell in ws3[1]:
        cell.font, cell.fill = head_font, head_fill
    for row in HELP:
        ws3.append(list(row))
    ws3.column_dimensions["A"].width, ws3.column_dimensions["B"].width = 28, 100
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


# --- çözümleme (yalnızca dosya) ---------------------------------------------------------------


def _where(row: Row, sheet: str, key: str | None = None) -> str:
    if key and key in row.coords:
        return f"{sheet}!{row.coords[key]}"
    return f"{sheet} satır {row.number}"


def _money(row: Row, key: str, sheet: str, issues: Issues, *, signed=False) -> int | None:
    cell = parse_cell(row.values.get(key))
    if cell.kind == EMPTY:
        return None
    if cell.kind != AMOUNT:
        issues.error(_where(row, sheet, key), f"Tutar okunamadı: {cell.raw!r}")
        return None
    for flag in cell.flags:
        if flag != "negative" or not signed:
            issues.warning(_where(row, sheet, key), f"{FLAG_TEXT.get(flag, flag)}: {cell.raw!r}")
    kurus = to_kurus(cell.value)
    if kurus is None:
        issues.error(_where(row, sheet, key), "Tutarda en fazla iki kuruş hanesi olabilir.")
    elif kurus < 0 and not signed:
        issues.error(_where(row, sheet, key), "Tutar eksi olamaz.")
        return None
    return kurus


def _int(row: Row, key: str, sheet: str, issues: Issues, *, min_value: int | None = None) -> int | None:
    raw = row.values.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        number = Decimal(str(raw).strip().replace(",", "."))
    except InvalidOperation:
        number = None
    if number is None or number != number.to_integral_value():
        issues.error(_where(row, sheet, key), f"Tam sayı olmalı: {raw!r}")
        return None
    if min_value is not None and number < min_value:
        issues.error(_where(row, sheet, key), f"{min_value} ya da daha büyük olmalı.")
        return None
    return int(number)


def _person(row: Row, prefix: str, sheet: str, issues: Issues) -> dict | None:
    first, last = row.text(f"{prefix}_first"), row.text(f"{prefix}_last")
    phone_raw, email = row.values.get(f"{prefix}_phone"), row.text(f"{prefix}_email").lower()
    who = "Malik" if prefix == "owner" else "Kiracı"
    if not (first or last or row.text(f"{prefix}_phone") or email):
        return None
    if not first or not last:
        issues.error(_where(row, sheet, f"{prefix}_first"), f"{who} adı ve soyadı birlikte girilmeli.")
        return None
    phone = normalize_phone(phone_raw)
    if phone and not is_valid_phone(phone):
        issues.error(_where(row, sheet, f"{prefix}_phone"), f"{who} telefonu geçersiz (05xx xxx xx xx).")
        phone = ""
    if email:
        try:
            validate_email(email)
        except ValidationError:
            issues.error(_where(row, sheet, f"{prefix}_email"), f"{who} e-postası geçersiz.")
            email = ""
    return {"first": first[:80], "last": last[:80], "phone": phone, "email": email}


def unit_key(block: str, number: str) -> tuple[str, str]:
    return norm(block), norm(number)


def parse(data: bytes) -> tuple[dict, Issues]:
    issues = Issues()
    wb = open_workbook(data)
    units_ws, opening_ws = find_sheet(wb, UNITS_SHEET), find_sheet(wb, OPENING_SHEET)
    if units_ws is None and opening_ws is None:
        raise SheetError("Dosyada 'Bölümler' ya da 'Açılış bakiyeleri' sayfası yok. Şablonu indirip kullanın.")

    units, seen = [], {}
    if units_ws is not None:
        table = read_table(units_ws, UNIT_COLUMNS, {"number"})
        sheet = table.sheet
        for row in table.rows:
            number = row.text("number")
            if not number:
                issues.error(_where(row, sheet, "number"), "Bölüm No boş.")
                continue
            block = row.text("block")
            key = unit_key(block, number)
            if key in seen:
                issues.error(_where(row, sheet, "number"), f"Bu bölüm dosyada iki kez var (satır {seen[key]}).")
                continue
            seen[key] = row.number
            kind_text = row.text("kind")
            kind = KINDS.get(norm(kind_text), None) if kind_text else "apartment"
            if kind is None:
                issues.error(_where(row, sheet, "kind"), f"Tür tanınmadı: {kind_text!r} (Daire, Dükkan, Ofis...)")
                kind = "apartment"
            area = None
            area_cell = parse_cell(row.values.get("area"))
            if area_cell.kind == AMOUNT and area_cell.value > 0:
                area = str(area_cell.value.quantize(Decimal("0.01")))
            elif area_cell.kind != EMPTY:
                issues.error(_where(row, sheet, "area"), f"Brüt m² okunamadı: {area_cell.raw!r}")
            num = _int(row, "share_num", sheet, issues, min_value=1)
            den = _int(row, "share_den", sheet, issues, min_value=1)
            if (num is None) != (den is None):
                issues.error(_where(row, sheet, "share_num"), "Arsa payının hem payını hem paydasını girin.")
                num = den = None
            elif num is not None and num > den:
                issues.error(_where(row, sheet, "share_num"), "Arsa payı, paydadan büyük olamaz.")
            owner = _person(row, "owner", sheet, issues)
            tenant = _person(row, "tenant", sheet, issues)
            liable_text = norm(row.text("liable"))
            if liable_text in ("", "malik", "kiracı", "kiraci"):
                liable = {"malik": "owner", "kiracı": "tenant", "kiraci": "tenant"}.get(liable_text)
            else:
                issues.error(_where(row, sheet, "liable"), "Borç sorumlusu 'Malik' ya da 'Kiracı' olmalı.")
                liable = None
            liable = liable or ("tenant" if tenant else "owner")
            if liable == "tenant" and not tenant:
                issues.error(_where(row, sheet, "liable"), "Borç sorumlusu kiracı ama kiracı bilgisi yok.")
            units.append(
                {
                    "row": row.number,
                    "block": block[:60],
                    "number": number[:20],
                    "floor": _int(row, "floor", sheet, issues),
                    "kind": kind,
                    "type": row.text("type")[:60],
                    "type_amount": _money(row, "type_amount", sheet, issues),
                    "area": area,
                    "share_num": num,
                    "share_den": den,
                    "owner": owner,
                    "tenant": tenant,
                    "liable": liable,
                }
            )

    openings = []
    if opening_ws is not None:
        table = read_table(opening_ws, OPENING_COLUMNS, {"number", "amount"})
        sheet = table.sheet
        for row in table.rows:
            number = row.text("number")
            if not number:
                issues.error(_where(row, sheet, "number"), "Bölüm No boş.")
                continue
            amount = _money(row, "amount", sheet, issues, signed=True)
            if not amount:
                if amount == 0:
                    issues.info(_where(row, sheet, "amount"), "Sıfır tutar atlandı.")
                elif amount is None and parse_cell(row.values.get("amount")).kind == EMPTY:
                    issues.error(_where(row, sheet, "amount"), "Tutar boş.")
                continue
            period = parse_month(row.values.get("period"))
            if amount > 0 and period is None:
                issues.error(_where(row, sheet, "period"), "Borcun dönemi okunamadı (ör. 2026-09).")
                continue
            openings.append(
                {
                    "row": row.number,
                    "block": row.text("block"),
                    "number": number,
                    "period": period.isoformat() if period else None,
                    "amount": amount,
                    "description": row.text("description")[:200],
                }
            )
    if not units and not openings and not issues.has_errors:
        raise SheetError("Dosyada okunacak satır yok.")
    return {"units": units, "openings": openings}, issues


# --- plan (veritabanıyla karşılaştırma) -------------------------------------------------------


@dataclass
class Plan:
    issues: Issues
    new_blocks: list[str] = field(default_factory=list)
    new_types: dict[str, int | None] = field(default_factory=dict)
    new_units: list[dict] = field(default_factory=list)
    existing_units: list[dict] = field(default_factory=list)
    people_new: int = 0
    people_reused: int = 0
    occupancies: int = 0
    debts: list[dict] = field(default_factory=list)
    advances: list[dict] = field(default_factory=list)
    grid_rows: list = field(default_factory=list)  # yalnızca aylık çizelge önizlemesi

    @property
    def summary(self) -> dict:
        return {
            "yeni_blok": len(self.new_blocks),
            "yeni_tip": len(self.new_types),
            "yeni_bolum": len(self.new_units),
            "mevcut_bolum": len(self.existing_units),
            "yeni_kisi": self.people_new,
            "eslesen_kisi": self.people_reused,
            "iliski": self.occupancies,
            "acilis_borc_kalemi": len(self.debts),
            "acilis_borc_toplam": sum(d["amount"] for d in self.debts),
            "acilis_avans_bolum": len(self.advances),
            "acilis_avans_toplam": sum(a["amount"] for a in self.advances),
        }


def _person_key(p: dict) -> tuple[str, str, str]:
    return norm(p["first"]), norm(p["last"]), p["phone"]


def existing_people_index(org_people) -> dict[tuple[str, str, str], Person]:
    return {(norm(p.first_name), norm(p.last_name), p.phone): p for p in org_people}


def plan(site, payload: dict, options: dict) -> Plan:
    issues = Issues()
    p = Plan(issues)
    blocks = {norm(b.name): b for b in Block.objects.filter(site=site)}
    types = {norm(t.name): t for t in UnitType.objects.filter(site=site)}
    units = {
        unit_key(u.block.name if u.block_id else "", u.number): u
        for u in Unit.objects.filter(site=site).select_related("block")
    }
    people = existing_people_index(Person.objects.filter(is_anonymized=False))
    file_people: set[tuple[str, str, str]] = set()
    sheet = UNITS_SHEET[0]

    for row in payload.get("units", []):
        where = f"{sheet} satır {row['row']}"
        key = unit_key(row["block"], row["number"])
        if key in units:
            p.existing_units.append(row)
            if row["owner"] or row["tenant"]:
                issues.warning(where, "Bölüm zaten kayıtlı; bölüm ve kişi bilgileri değiştirilmedi.")
            continue
        if row["block"] and norm(row["block"]) not in blocks | {norm(b): b for b in p.new_blocks}:
            p.new_blocks.append(row["block"])
        if row["type"]:
            tkey = norm(row["type"])
            if tkey in types:
                existing_amount = types[tkey].default_amount
                if row["type_amount"] is not None and existing_amount != row["type_amount"]:
                    issues.warning(
                        where,
                        f"'{row['type']}' tipi zaten var; aidatı ({format_try(existing_amount or 0)}) değiştirilmedi.",
                    )
            else:
                known = {norm(k): (k, v) for k, v in p.new_types.items()}
                if tkey in known:
                    name, amount = known[tkey]
                    if row["type_amount"] is not None and amount is not None and amount != row["type_amount"]:
                        issues.error(where, f"'{row['type']}' tipi için dosyada farklı aidatlar var.")
                    elif amount is None:
                        p.new_types[name] = row["type_amount"]
                else:
                    p.new_types[row["type"]] = row["type_amount"]
        p.new_units.append(row)
        for who in ("owner", "tenant"):
            person = row[who]
            if not person:
                continue
            p.occupancies += 1
            pkey = _person_key(person)
            if not person["phone"]:
                p.people_new += 1  # telefonsuz kişiler adı aynı olsa da ayrı kişi sayılır
                continue
            if pkey in file_people:
                continue
            file_people.add(pkey)
            if pkey in people:
                p.people_reused += 1
            else:
                p.people_new += 1

    new_keys = {unit_key(r["block"], r["number"]) for r in p.new_units}
    opening_day = date.fromisoformat(options["opening_date"]) if options.get("opening_date") else None
    osheet = OPENING_SHEET[0]
    per_unit_sign: dict[tuple[str, str], set[int]] = {}
    for row in payload.get("openings", []):
        where = f"{osheet} satır {row['row']}"
        key = unit_key(row["block"], row["number"])
        if key not in units and key not in new_keys:
            label = f"{row['block']} {row['number']}".strip()
            issues.error(where, f"'{label}' bölümü ne sistemde ne de Bölümler sayfasında var.")
            continue
        if key in units and units[key].is_archived:
            issues.error(where, "Bölüm arşivde; açılış bakiyesi girilemez.")
            continue
        per_unit_sign.setdefault(key, set()).add(1 if row["amount"] > 0 else -1)
        if row["amount"] > 0:
            if opening_day and date.fromisoformat(row["period"]) > opening_day:
                issues.error(where, "Borç dönemi açılış tarihinden sonra olamaz.")
                continue
            p.debts.append({**row, "key": key})
        else:
            p.advances.append({**row, "key": key, "amount": -row["amount"]})
    for key, signs in per_unit_sign.items():
        if len(signs) > 1:
            issues.error(
                osheet, f"'{' '.join(key).strip()}' için hem borç hem avans var; net tutarı tek satırda girin."
            )
    merged: dict[tuple[str, str], dict] = {}
    for adv in p.advances:
        if adv["key"] in merged:
            merged[adv["key"]]["amount"] += adv["amount"]
        else:
            merged[adv["key"]] = dict(adv)
    p.advances = list(merged.values())

    if p.debts or p.advances:
        if opening_day is None:
            issues.error("Seçenekler", "Açılış bakiyeleri için açılış tarihini girin.")
        else:
            until = ledger.locked_until(site)
            if until and opening_day <= until:
                issues.error("Seçenekler", f"Açılış tarihi kilitli dönemde ({until:%d.%m.%Y} ve öncesi kilitli).")
    if not p.new_units and not p.debts and not p.advances and not issues.has_errors:
        issues.warning("Dosya", "Eklenecek yeni bir şey yok; tüm bölümler zaten kayıtlı.")
    return p


class ApplyError(ValueError):
    pass


def apply(site, payload: dict, options: dict, user) -> Plan:
    with transaction.atomic():
        p = plan(site, payload, options)
        if p.issues.has_errors:
            raise ApplyError("Dosyada düzeltilmesi gereken hatalar var; önizlemeyi yenileyin.")
        start = date.fromisoformat(options["start_date"])
        blocks = {norm(b.name): b for b in Block.objects.filter(site=site)}
        next_order = max([b.order for b in blocks.values()], default=-1) + 1
        for name in p.new_blocks:
            blocks[norm(name)] = Block.objects.create(site=site, name=name, order=next_order)
            next_order += 1
        types = {norm(t.name): t for t in UnitType.objects.filter(site=site)}
        for name, amount in p.new_types.items():
            types[norm(name)] = UnitType.objects.create(site=site, name=name, default_amount=amount)
        people = existing_people_index(Person.objects.filter(is_anonymized=False))
        for row in p.new_units:
            unit = Unit.objects.create(
                site=site,
                block=blocks.get(norm(row["block"])) if row["block"] else None,
                number=row["number"],
                floor=row["floor"],
                kind=row["kind"],
                unit_type=types.get(norm(row["type"])) if row["type"] else None,
                gross_area=Decimal(row["area"]) if row["area"] else None,
                share_numerator=row["share_num"],
                share_denominator=row["share_den"],
            )
            for who, relation in (("owner", Occupancy.Relation.OWNER), ("tenant", Occupancy.Relation.TENANT)):
                data = row[who]
                if not data:
                    continue
                # Aynı ad + aynı telefon = aynı kişi (iki dairenin maliki); telefonsuzlar hep yeni kişi.
                pkey = _person_key(data)
                person = people.get(pkey) if data["phone"] else None
                if person is None:
                    person = Person.objects.create(
                        first_name=data["first"], last_name=data["last"], phone=data["phone"], email=data["email"]
                    )
                    if data["phone"]:
                        people[pkey] = person
                Occupancy.objects.create(
                    unit=unit, person=person, relation=relation, start_date=start, is_liable=row["liable"] == who
                )

        post_openings(site, p, options, user)
    return p


def post_openings(site, p: Plan, options: dict, user) -> None:
    """Planın açılış borç ve avanslarını deftere yazar (kurulum şablonu ve aylık çizelge ortak)."""
    from apps.billing.services import BillingError

    if not (p.debts or p.advances):
        return
    units = {
        unit_key(u.block.name if u.block_id else "", u.number): u
        for u in Unit.objects.filter(site=site).select_related("block")
    }
    day = date.fromisoformat(options["opening_date"])
    try:
        opening.post_opening(
            site,
            day=day,
            debts=[
                opening.OpeningDebt(units[d["key"]], date.fromisoformat(d["period"]), d["amount"], d["description"])
                for d in p.debts
            ],
            advances=[opening.OpeningAdvance(units[a["key"]], a["amount"]) for a in p.advances],
            user=user,
        )
        if options.get("lock"):
            until = ledger.locked_until(site)
            if not until or until < day:
                ledger.lock_period(site, day, user)
    except (BillingError, ledger.LedgerError) as exc:
        raise ApplyError(str(exc)) from exc
