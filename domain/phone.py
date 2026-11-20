"""Telefon numarası: tek biçim (`05551234567`). Sakin girişi bu biçimle eşleştirir."""

from __future__ import annotations

import re

_VALID = re.compile(r"0[2-5]\d{9}")


def normalize_phone(value: object) -> str:
    """'+90 555 123 45 67', '0(555) 123-45-67', '5551234567' → '05551234567'.

    Çözülemeyen girdide rakamları döndürür; geçerliliği `is_valid_phone` söyler."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # Excel sayı hücresi: 5551234567.0
    digits = re.sub(r"\D", "", str(value))
    if digits.startswith("0090"):
        digits = digits[2:]
    if digits.startswith("90") and len(digits) == 12:
        digits = "0" + digits[2:]
    if len(digits) == 10 and not digits.startswith("0"):
        digits = "0" + digits
    return digits


def is_valid_phone(phone: str) -> bool:
    return bool(_VALID.fullmatch(phone or ""))


def is_mobile(phone: str) -> bool:
    return is_valid_phone(phone) and phone.startswith("05")


def mask_phone(phone: str) -> str:
    """'05551234567' → '0555 *** ** 67' (ekranda ve kayıtlarda tam numara göstermemek için)."""
    if not is_valid_phone(phone):
        return "—"
    return f"{phone[:4]} *** ** {phone[-2:]}"
