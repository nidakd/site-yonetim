"""Tahsilat eşleştirme: bir ödemenin hangi borçları hangi sırayla kapattığı.

Varsayılan kurallar (Plan §5.5):
1. Ödeyenin işaret ettiği borçlar önce kapanır.
2. Sonra vadesi en eski borç (FIFO).
3. Aynı vadede tür önceliği küçük olan önce (ör. aidat 10 → yakıt 20 → gecikme en son).
4. Hâlâ eşitse borcun kimliği (önce oluşturulan önce).
5. Artan tutar avans olarak kalır.
"""

from __future__ import annotations

from collections.abc import Collection, Hashable, Sequence
from dataclasses import dataclass
from datetime import date


class AllocationError(ValueError):
    pass


@dataclass(frozen=True)
class OpenItem:
    id: Hashable
    due_date: date
    priority: int
    remaining: int  # kuruş, > 0
    sort_id: int = 0


@dataclass(frozen=True)
class AllocationResult:
    lines: list[tuple[Hashable, int]]  # (borç kimliği, kapatılan tutar)
    leftover: int  # avansa kalan

    @property
    def allocated(self) -> int:
        return sum(amount for _, amount in self.lines)


def allocate(amount: int, items: Sequence[OpenItem], preferred: Collection[Hashable] = ()) -> AllocationResult:
    if amount < 0:
        raise AllocationError("Ödeme tutarı negatif olamaz.")
    if any(item.remaining < 0 for item in items):
        raise AllocationError("Borç kalanı negatif olamaz.")
    ordered = sorted(items, key=lambda i: (i.id not in preferred, i.due_date, i.priority, i.sort_id))
    lines: list[tuple[Hashable, int]] = []
    left = amount
    for item in ordered:
        if left == 0:
            break
        if item.remaining == 0:
            continue
        take = min(left, item.remaining)
        lines.append((item.id, take))
        left -= take
    return AllocationResult(lines, left)
