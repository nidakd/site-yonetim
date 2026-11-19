"""Para: kuruş (tam sayı) ↔ Türkçe gösterim (`1.234,56 ₺`)."""

from __future__ import annotations

import re
from decimal import Decimal
from fractions import Fraction

_AMOUNT_RE = re.compile(r"^\d{1,3}(\.\d{3})*(,\d{1,2})?$|^\d+(,\d{1,2})?$|^\d+(\.\d{1,2})?$")


class InvalidAmount(ValueError):
    pass


def format_try(kurus: int, *, symbol: bool = True) -> str:
    """12345678 → '123.456,78 ₺'. Negatif tutarlar başta eksi işaretiyle gösterilir."""
    sign = "-" if kurus < 0 else ""
    lira, kr = divmod(abs(int(kurus)), 100)
    grouped = f"{lira:,}".replace(",", ".")
    text = f"{sign}{grouped},{kr:02d}"
    return f"{text} ₺" if symbol else text


def parse_try(text: str) -> int:
    """Kullanıcının yazdığı tutarı kuruşa çevirir.

    Kabul edilenler: '1.234,56', '1234,56', '1234', '1234.56' (ondalık nokta, en fazla iki hane),
    başında/sonunda boşluk ve '₺' / 'TL'. Negatif tutar ve ikiden fazla kuruş hanesi reddedilir.
    """
    cleaned = text.strip().replace("₺", "").replace("TL", "").replace("tl", "").replace(" ", "")
    if not cleaned or not _AMOUNT_RE.match(cleaned):
        raise InvalidAmount(f"Geçersiz tutar: {text!r}")
    if "," in cleaned:
        lira, kr = cleaned.replace(".", "").split(",")
    elif re.match(r"^\d+\.\d{1,2}$", cleaned):
        lira, kr = cleaned.split(".")
    else:
        lira, kr = cleaned.replace(".", ""), "0"
    return int(lira) * 100 + int(kr.ljust(2, "0"))


def round_half_up(value: Fraction) -> int:
    """Kesirli kuruşu en yakın tam kuruşa yuvarlar (yarım yukarı). Negatifte simetrik."""
    q, r = divmod(abs(value.numerator), value.denominator)
    if 2 * r >= value.denominator:
        q += 1
    return -q if value < 0 else q


def to_fraction(value: Decimal | int | str) -> Fraction:
    """Decimal/int/str değeri kesin kesre çevirir (float kabul edilmez)."""
    if isinstance(value, float):
        raise TypeError("Para ve oran hesabında float kullanılmaz.")
    return Fraction(Decimal(value)) if not isinstance(value, int) else Fraction(value)
