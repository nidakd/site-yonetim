"""Excel hücresi yorumlama (eski `aidat_takip` çözümleyicisinden taşındı).

Temel kural: emin olunmayan hiçbir şey sessizce tutara çevrilmez. Hücre ya `empty` (boş),
ya `amount` (tutar) ya da `note` (tutar olarak okunamayan metin/tarih) olur. Belirsiz
okumalar bayrakla işaretlenir ve içe aktarma önizlemesinde kullanıcıya gösterilir.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

EMPTY = "empty"
AMOUNT = "amount"
NOTE = "note"

F_PARTIAL = "partial"  # "kısmi" geçiyor
F_REFUND = "refund"  # "iade" geçiyor, tutar eksiye çevrildi
F_EXPRESSION = "expression"  # "350+230-50" gibi toplama/çıkarma
F_AMBIGUOUS = "ambiguous_separator"  # "1.150": 1150 mi 1,15 mi?
F_NEGATIVE = "negative"  # iade denmeden eksi tutar
F_DATE = "date"  # hücrede tutar yerine tarih var

FLAG_TEXT = {
    F_PARTIAL: '"kısmi" yazıyor',
    F_REFUND: "iade olarak eksi sayıldı",
    F_EXPRESSION: "toplama/çıkarma ifadesi hesaplandı",
    F_AMBIGUOUS: "binlik ayracı belirsiz (ör. 1.150)",
    F_NEGATIVE: "eksi tutar",
    F_DATE: "tutar yerine tarih var",
}


@dataclass(frozen=True)
class ParsedCell:
    kind: str
    amount: Decimal | None = None
    raw: str = ""
    flags: tuple[str, ...] = ()
    when: date | None = None

    @property
    def value(self) -> Decimal:
        """Hesapta kullanılacak tutar; tutar değilse 0."""
        return self.amount if self.amount is not None else Decimal(0)


_DASHES = {"-", "–", "—", "−"}
_LETTER = "a-zçğıöşüâîû"
_CURRENCY = re.compile(rf"₺|(?<![{_LETTER}])(?:tl|try|lira)(?![{_LETTER}])")
_PARTIAL = re.compile(rf"(?<![{_LETTER}])k[ıi]sm[iî](?:\s+[öo]deme)?(?![{_LETTER}])")
_REFUND = re.compile(rf"(?<![{_LETTER}])iade(?:si)?(?![{_LETTER}])")
_EXPRESSION = re.compile(r"[+-]?[0-9][0-9.,]*(?:[+-][0-9][0-9.,]*)*")
_TERM = re.compile(r"([+-]?)([0-9][0-9.,]*)")

_TIME_SUFFIX = r"(?:[ T][0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)?"
_DATE_DMY = re.compile(rf"([0-9]{{1,2}})[./-]([0-9]{{1,2}})[./-]([0-9]{{4}}|[0-9]{{2}}){_TIME_SUFFIX}")
_DATE_YMD = re.compile(rf"([0-9]{{4}})[./-]([0-9]{{1,2}})[./-]([0-9]{{1,2}}){_TIME_SUFFIX}")
_DATE_NAMED = re.compile(rf"([0-9]{{1,2}})\s+([{_LETTER}]+)\.?\s+([0-9]{{4}})")
_MONTH_YM = re.compile(r"([0-9]{4})[./-]([0-9]{1,2})")
_MONTH_MY = re.compile(r"([0-9]{1,2})[./-]([0-9]{4})")
_MONTH_NAMED = re.compile(rf"([{_LETTER}]+)\.?\s*([0-9]{{4}})?")

MONTHS_TR = (
    "ocak",
    "şubat",
    "mart",
    "nisan",
    "mayıs",
    "haziran",
    "temmuz",
    "ağustos",
    "eylül",
    "ekim",
    "kasım",
    "aralık",
)


def tr_lower(text: str) -> str:
    """Türkçe'ye uygun küçük harf (İ→i, I→ı)."""
    return text.replace("İ", "i").replace("I", "ı").lower()


def norm(text: object) -> str:
    """Başlık karşılaştırması için: Türkçe küçük harf, tek boşluk, '*' ve ':' atılır."""
    return " ".join(tr_lower(str(text or "")).replace("*", " ").replace(":", " ").split())


_FOLD = str.maketrans("ıiçğöşüâîû", "iicgosuaiu")


def fold(text: object) -> str:
    """Türkçe harflerden bağımsız karşılaştırma: 'DAIRE', 'Daire', 'DAİRE' → 'daire'; 'ŞUBAT', 'SUBAT' → 'subat'."""
    return norm(text).translate(_FOLD)


def month_index(name: str) -> int | None:
    """'Eylül', 'eyl', 'EYLÜL', 'EYLUL' → 9. Tanınmazsa None."""
    s = fold(name)
    for i, month in enumerate(MONTHS_TR, start=1):
        m = month.translate(_FOLD)
        if s == m or (len(s) >= 3 and m.startswith(s)):
            return i
    return None


def parse_number(token: str) -> tuple[Decimal, bool] | None:
    """İşaretsiz bir sayı metnini çözer: `(değer, belirsiz_mi)` ya da None.

    Desteklenen biçimler: `1150`, `1150,5`, `1150.50`, `1.150,00` (Türkçe), `1,150.00`
    (İngilizce), `1.150.000`. `1.150` gibi tek ayraçlı ve üç haneli yazımlar binlik kabul
    edilir ama *belirsiz* olarak işaretlenir."""
    if not re.fullmatch(r"[0-9](?:[0-9.,]*[0-9])?", token):
        return None
    has_dot, has_comma = "." in token, "," in token
    if has_dot and has_comma:
        dec = "," if token.rfind(",") > token.rfind(".") else "."
        grp = "." if dec == "," else ","
        whole, _, frac = token.rpartition(dec)
        if dec in whole or len(frac) not in (1, 2):
            return None
        if not re.fullmatch(rf"[0-9]{{1,3}}(?:{re.escape(grp)}[0-9]{{3}})+", whole):
            return None
        return Decimal(f"{whole.replace(grp, '')}.{frac}"), False
    if has_dot or has_comma:
        sep = "." if has_dot else ","
        if token.count(sep) == 1:
            whole, frac = token.split(sep)
            if len(frac) in (1, 2):
                return Decimal(f"{whole}.{frac}"), False
            if len(frac) == 3 and 1 <= len(whole) <= 3:
                return Decimal(whole + frac), True
            return None
        if re.fullmatch(rf"[0-9]{{1,3}}(?:{re.escape(sep)}[0-9]{{3}})+", token):
            return Decimal(token.replace(sep, "")), False
        return None
    return Decimal(token), False


def parse_date(text: str) -> date | None:
    """`15.03.2025`, `15/03/25`, `2025-03-15`, `15 Mart 2025` → tarih."""
    s = tr_lower(text.strip())
    try:
        if m := _DATE_DMY.fullmatch(s):
            day, month, year = int(m[1]), int(m[2]), int(m[3])
            if year < 100:
                year += 2000
            return date(year, month, day)
        if m := _DATE_YMD.fullmatch(s):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := _DATE_NAMED.fullmatch(s):
            month = month_index(m[2])
            if month:
                return date(int(m[3]), month, int(m[1]))
    except ValueError:
        return None
    return None


def parse_month(value: object) -> date | None:
    """Dönem hücresi → ayın ilk günü. `2026-09`, `09.2026`, `Eylül 2026`, tarih hücresi.
    Yılsız ay adı (`Eylül`) için None döner (yılı çağıran belirler)."""
    if isinstance(value, datetime):
        return value.date().replace(day=1)
    if isinstance(value, date):
        return value.replace(day=1)
    if value is None:
        return None
    s = norm(value)
    try:
        if m := _MONTH_YM.fullmatch(s):
            return date(int(m[1]), int(m[2]), 1)
        if m := _MONTH_MY.fullmatch(s):
            return date(int(m[2]), int(m[1]), 1)
        full = parse_date(s)
        if full:
            return full.replace(day=1)
        if (m := _MONTH_NAMED.fullmatch(s)) and m[2]:
            month = month_index(m[1])
            return date(int(m[2]), month, 1) if month else None
    except ValueError:
        return None
    return None


def _parse_text(raw: str) -> ParsedCell:
    s = raw.replace(" ", " ").replace(" ", " ").strip()
    if not s or s in _DASHES:
        return ParsedCell(EMPTY, raw=raw)

    when = parse_date(s)
    if when is not None:
        return ParsedCell(NOTE, raw=raw, flags=(F_DATE,), when=when)

    flags: list[str] = []
    body = tr_lower(s).replace("−", "-").replace("–", "-")
    body, n = _PARTIAL.subn(" ", body)
    if n:
        flags.append(F_PARTIAL)
    body, n = _REFUND.subn(" ", body)
    if n:
        flags.append(F_REFUND)
    body = _CURRENCY.sub(" ", body)
    # Yalnızca işleçlerin çevresindeki boşluklar atılır; "350 230" tek sayı sayılmaz.
    body = re.sub(r"\s*([+-])\s*", r"\1", body.strip())

    if not _EXPRESSION.fullmatch(body):
        return ParsedCell(NOTE, raw=raw, flags=tuple(flags))

    total = Decimal(0)
    terms = _TERM.findall(body)
    for sign, token in terms:
        parsed = parse_number(token)
        if parsed is None:
            return ParsedCell(NOTE, raw=raw, flags=tuple(flags))
        number, ambiguous = parsed
        if ambiguous and F_AMBIGUOUS not in flags:
            flags.append(F_AMBIGUOUS)
        total += -number if sign == "-" else number

    if len(terms) > 1:
        flags.append(F_EXPRESSION)
    if F_REFUND in flags:
        total = -abs(total)
    elif total < 0:
        flags.append(F_NEGATIVE)
    return ParsedCell(AMOUNT, amount=total, raw=raw, flags=tuple(flags))


def parse_cell(value: object) -> ParsedCell:
    """Excel'den gelen ham hücre değerini yorumlar. Hiçbir girdide hata fırlatmaz."""
    if value is None:
        return ParsedCell(EMPTY)
    if isinstance(value, bool):
        return ParsedCell(NOTE, raw=str(value))
    if isinstance(value, int | float | Decimal):
        raw = str(value)
        if isinstance(value, float) and not math.isfinite(value):
            return ParsedCell(NOTE, raw=raw)
        number = Decimal(raw)
        if not number.is_finite():
            return ParsedCell(NOTE, raw=raw)
        return ParsedCell(AMOUNT, amount=number, raw=raw, flags=(F_NEGATIVE,) if number < 0 else ())
    if isinstance(value, datetime):
        return ParsedCell(NOTE, raw=value.isoformat(sep=" "), flags=(F_DATE,), when=value.date())
    if isinstance(value, date):
        return ParsedCell(NOTE, raw=value.isoformat(), flags=(F_DATE,), when=value)
    if isinstance(value, time):
        return ParsedCell(NOTE, raw=value.isoformat())
    if isinstance(value, str):
        return _parse_text(value)
    return ParsedCell(NOTE, raw=str(value))


def to_kurus(amount: Decimal) -> int | None:
    """Lira tutarını kuruşa çevirir; ikiden fazla kuruş hanesi varsa (ör. 12,345) None."""
    scaled = amount * 100
    if scaled != scaled.to_integral_value():
        return None
    return int(scaled)


def owed_by_month(fees: list[int], paid: int) -> tuple[list[int], int]:
    """Aylık çizelgeden yaşlandırma: ödemeler en eski aydan başlayarak kapatır.

    `fees`: her ayın borcu (kuruş, eskiden yeniye), `paid`: toplam ödenen (iade düşülmüş).
    Dönüş: her ayın kalan borcu ve artan (avans) tutar. Σ kalan − avans = Σ borç − ödenen."""
    remaining, pool = [], max(paid, 0)
    for fee in fees:
        take = min(fee, pool)
        pool -= take
        remaining.append(fee - take)
    if paid < 0:  # net iade: en eski aya borç olarak eklenir (çizelgeyle bakiye tutsun)
        if not remaining:
            raise ValueError("En az bir ay gerekli.")
        remaining[0] += -paid
    return remaining, pool
