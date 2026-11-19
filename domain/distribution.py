"""Borç dağıtımı: toplam tutarı paylara bölmek ve özel aidat anlaşmalarını uygulamak.

Kurallar
- Toplam bir anahtara (eşit, arsa payı, m²) göre bölünürken **en büyük kalan yöntemi**
  kullanılır: herkes payının tam kuruş kısmını alır, artan kuruşlar kesir kısmı en büyük olan
  paylara birer birer verilir. Dağıtılan kuruşların toplamı her zaman girilen toplama eşittir.
- Eşit kalanlarda girdi sırası belirleyicidir (deterministik: aynı girdi → aynı sonuç).
- **Muaf** bölümler paydaya girmez; payları diğer bölümlere dağılır.
- **Sabit tutar** ve **indirim** anlaşmaları payı belirlendikten sonra uygulanır; aradaki fark
  diğer bölümlere yüklenmez (sakinin özel aidatı komşusunun borcunu artırmaz).
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from .money import round_half_up


class DistributionError(ValueError):
    pass


def largest_remainder[K: Hashable](total: int, weights: Mapping[K, Fraction | int]) -> dict[K, int]:
    """`total` kuruşu ağırlıklara göre böler. Σsonuç == total; her pay ⌊kesin pay⌋ ya da ⌈kesin pay⌉."""
    if total < 0:
        raise DistributionError("Dağıtılacak tutar negatif olamaz.")
    if any(w < 0 for w in weights.values()):
        raise DistributionError("Ağırlık negatif olamaz.")
    weight_sum = sum((Fraction(w) for w in weights.values()), Fraction(0))
    if not weights or weight_sum == 0:
        raise DistributionError("Dağıtım için en az bir sıfırdan büyük ağırlık gerekli.")

    shares: dict[K, int] = {}
    remainders: list[tuple[Fraction, int, K]] = []
    for index, (key, weight) in enumerate(weights.items()):
        exact = Fraction(total) * Fraction(weight) / weight_sum
        floor = exact.numerator // exact.denominator
        shares[key] = floor
        remainders.append((exact - floor, -index, key))

    leftover = total - sum(shares.values())
    for _, _, key in sorted(remainders, reverse=True)[:leftover]:
        shares[key] += 1
    return shares


class AgreementKind(StrEnum):
    FIXED = "fixed"  # bu bölüm dönem başına sabit tutar öder
    DISCOUNT = "discount"  # payından yüzde indirim
    EXEMPT = "exempt"  # muaf


@dataclass(frozen=True)
class Agreement:
    kind: AgreementKind
    amount: int = 0  # FIXED için kuruş
    percent: Fraction = Fraction(0)  # DISCOUNT için 0–100


@dataclass(frozen=True)
class Share:
    base: int  # anlaşma öncesi pay
    amount: int  # borçlandırılacak tutar
    agreement: Agreement | None = None


def apply_agreement(base: int, agreement: Agreement | None) -> int:
    if agreement is None:
        return base
    if agreement.kind is AgreementKind.EXEMPT:
        return 0
    if agreement.kind is AgreementKind.FIXED:
        if agreement.amount < 0:
            raise DistributionError("Sabit tutar negatif olamaz.")
        return agreement.amount
    if not 0 <= agreement.percent <= 100:
        raise DistributionError("İndirim oranı 0 ile 100 arasında olmalı.")
    return base - round_half_up(Fraction(base) * agreement.percent / 100)


def distribute_total[K: Hashable](
    total: int,
    weights: Mapping[K, Fraction | int],
    agreements: Mapping[K, Agreement] | None = None,
) -> dict[K, Share]:
    """Toplamı ağırlıklara göre böler; muaflar paydaya girmez, diğer anlaşmalar sonra uygulanır."""
    agreements = agreements or {}
    eligible = {k: w for k, w in weights.items() if _kind(agreements.get(k)) is not AgreementKind.EXEMPT}
    bases = largest_remainder(total, eligible) if eligible else {}
    return {
        k: Share(bases.get(k, 0), apply_agreement(bases.get(k, 0), agreements.get(k)), agreements.get(k))
        for k in weights
    }


def per_unit[K: Hashable](amounts: Mapping[K, int], agreements: Mapping[K, Agreement] | None = None) -> dict[K, Share]:
    """Her bölümün tutarı önceden belli (sabit tutar ya da bölüm tipinin aidatı); anlaşmalar uygulanır."""
    agreements = agreements or {}
    if any(a < 0 for a in amounts.values()):
        raise DistributionError("Tutar negatif olamaz.")
    return {k: Share(a, apply_agreement(a, agreements.get(k)), agreements.get(k)) for k, a in amounts.items()}


def _kind(agreement: Agreement | None) -> AgreementKind | None:
    return agreement.kind if agreement else None


def total_of[K: Hashable](shares: Mapping[K, Share] | Sequence[Share]) -> int:
    values = shares.values() if isinstance(shares, Mapping) else shares
    return sum(s.amount for s in values)
