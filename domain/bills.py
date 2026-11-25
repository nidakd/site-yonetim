"""Ödenecek fatura hesabı (kuruş). Fatura tutarı ödemelerle kapanır; fazla ödeme olmaz."""

from __future__ import annotations

from collections.abc import Iterable


class BillError(ValueError):
    pass


def remaining(amount: int, payments: Iterable[int]) -> int:
    paid = sum(payments)
    if amount <= 0 or paid < 0 or paid > amount:
        raise BillError("Fatura tutarı ve ödemeler tutarsız.")
    return amount - paid


def check_payment(amount: int, payments: Iterable[int], new_payment: int) -> int:
    """Yeni ödemeden sonra kalan tutarı döndürür; kalandan fazla ödeme reddedilir."""
    left = remaining(amount, payments)
    if new_payment <= 0:
        raise BillError("Ödeme tutarı sıfırdan büyük olmalı.")
    if new_payment > left:
        raise BillError(f"Ödeme kalan tutardan ({left / 100:.2f} TL) fazla olamaz.")
    return left - new_payment
