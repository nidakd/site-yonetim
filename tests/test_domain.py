"""Saf alan fonksiyonları: para biçimi, dağıtım (en büyük kalan), eşleştirme. Veritabanı gerekmez."""

from datetime import date
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from domain.allocation import AllocationError, OpenItem, allocate
from domain.distribution import (
    Agreement,
    AgreementKind,
    DistributionError,
    apply_agreement,
    distribute_total,
    largest_remainder,
    per_unit,
    total_of,
)
from domain.money import InvalidAmount, format_try, parse_try, round_half_up

# --- para -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kurus", "text"),
    [(0, "0,00 ₺"), (5, "0,05 ₺"), (123456, "1.234,56 ₺"), (100000000, "1.000.000,00 ₺"), (-2550, "-25,50 ₺")],
)
def test_format_try(kurus, text):
    assert format_try(kurus) == text


@pytest.mark.parametrize(
    ("text", "kurus"),
    [
        ("1.234,56", 123456),
        ("1234,56", 123456),
        ("1234,5", 123450),
        ("1234", 123400),
        ("1.000", 100000),
        ("12.50", 1250),
        (" 750 ₺ ", 75000),
        ("750 TL", 75000),
        ("0,01", 1),
    ],
)
def test_parse_try(text, kurus):
    assert parse_try(text) == kurus


@pytest.mark.parametrize("text", ["", "-5", "1,234", "abc", "1.23.4", "12,3,4", "1e3"])
def test_parse_try_rejects(text):
    with pytest.raises(InvalidAmount):
        parse_try(text)


@given(st.integers(min_value=0, max_value=10**13))
def test_format_parse_roundtrip(kurus):
    assert parse_try(format_try(kurus)) == kurus


def test_round_half_up():
    assert round_half_up(Fraction(5, 2)) == 3
    assert round_half_up(Fraction(-5, 2)) == -3
    assert round_half_up(Fraction(249, 100)) == 2
    assert round_half_up(Fraction(7)) == 7


# --- en büyük kalan ---------------------------------------------------------------------------


def test_equal_split_gives_leftover_to_first_in_order():
    assert largest_remainder(100, {"a": 1, "b": 1, "c": 1}) == {"a": 34, "b": 33, "c": 33}


def test_share_split_by_arsa_payi():
    # 1000,00 ₺; arsa payları 120/1000, 80/1000, 50/1000 (paydalar farklı da olabilir)
    shares = largest_remainder(100000, {"d1": Fraction(120, 1000), "d2": Fraction(8, 100), "d3": Fraction(1, 20)})
    assert shares == {"d1": 48000, "d2": 32000, "d3": 20000}


def test_largest_remainder_prefers_largest_fraction():
    # kesin paylar: 3,3 / 3,3 / 3,4 → artan 1 kuruş en büyük kesirli olana (c)
    assert largest_remainder(10, {"a": 33, "b": 33, "c": 34}) == {"a": 3, "b": 3, "c": 4}


@pytest.mark.parametrize("weights", [{}, {"a": 0}, {"a": 0, "b": 0}])
def test_largest_remainder_needs_positive_weight(weights):
    with pytest.raises(DistributionError):
        largest_remainder(100, weights)


def test_largest_remainder_rejects_negative():
    with pytest.raises(DistributionError):
        largest_remainder(-1, {"a": 1})
    with pytest.raises(DistributionError):
        largest_remainder(1, {"a": -1, "b": 2})


weights_strategy = st.dictionaries(
    st.integers(min_value=1, max_value=500),
    st.fractions(min_value=0, max_value=1000, max_denominator=10_000),
    min_size=1,
    max_size=60,
).filter(lambda w: sum(w.values()) > 0)


@given(st.integers(min_value=0, max_value=10**12), weights_strategy)
def test_largest_remainder_properties(total, weights):
    shares = largest_remainder(total, weights)
    assert sum(shares.values()) == total  # kuruş kaybolmaz, fazladan kuruş doğmaz
    assert shares.keys() == weights.keys()
    weight_sum = sum(weights.values())
    for key, weight in weights.items():
        exact = Fraction(total) * weight / weight_sum
        assert exact - 1 < shares[key] < exact + 1  # her pay kesin payına 1 kuruştan yakın
        if weight == 0:
            assert shares[key] == 0


@given(st.integers(min_value=0, max_value=10**9), weights_strategy)
def test_largest_remainder_is_deterministic(total, weights):
    assert largest_remainder(total, weights) == largest_remainder(total, dict(weights))


# --- anlaşmalar -------------------------------------------------------------------------------


def test_agreements():
    assert apply_agreement(150000, None) == 150000
    assert apply_agreement(150000, Agreement(AgreementKind.EXEMPT)) == 0
    assert apply_agreement(150000, Agreement(AgreementKind.FIXED, amount=100000)) == 100000
    assert apply_agreement(150000, Agreement(AgreementKind.DISCOUNT, percent=Fraction(25))) == 112500
    assert apply_agreement(333, Agreement(AgreementKind.DISCOUNT, percent=Fraction(50))) == 166  # 166,5 → 167 indirim
    assert apply_agreement(100, Agreement(AgreementKind.DISCOUNT, percent=Fraction(100))) == 0
    with pytest.raises(DistributionError):
        apply_agreement(100, Agreement(AgreementKind.DISCOUNT, percent=Fraction(101)))


def test_exempt_units_leave_the_denominator():
    shares = distribute_total(90000, {"a": 1, "b": 1, "c": 1}, {"c": Agreement(AgreementKind.EXEMPT)})
    assert {k: s.amount for k, s in shares.items()} == {"a": 45000, "b": 45000, "c": 0}
    assert total_of(shares) == 90000


def test_fixed_and_discount_do_not_burden_neighbours():
    agreements = {
        "a": Agreement(AgreementKind.FIXED, amount=10000),
        "b": Agreement(AgreementKind.DISCOUNT, percent=Fraction(50)),
    }
    shares = distribute_total(90000, {"a": 1, "b": 1, "c": 1}, agreements)
    assert {k: (s.base, s.amount) for k, s in shares.items()} == {
        "a": (30000, 10000),
        "b": (30000, 15000),
        "c": (30000, 30000),
    }


def test_all_exempt_gives_zero():
    shares = distribute_total(1000, {"a": 1}, {"a": Agreement(AgreementKind.EXEMPT)})
    assert shares["a"].amount == 0


@given(
    st.integers(min_value=0, max_value=10**10),
    weights_strategy,
    st.sets(st.integers(min_value=1, max_value=500)),
)
def test_distribute_total_with_exemptions_keeps_total(total, weights, exempt_keys):
    agreements = {k: Agreement(AgreementKind.EXEMPT) for k in exempt_keys if k in weights}
    if all(weights[k] == 0 for k in weights if k not in agreements):
        return
    shares = distribute_total(total, weights, agreements)
    assert total_of(shares) == total
    assert all(shares[k].amount == 0 for k in agreements)


def test_per_unit():
    shares = per_unit({"a": 150000, "b": 150000}, {"b": Agreement(AgreementKind.DISCOUNT, percent=Fraction(10))})
    assert shares["a"].amount == 150000 and shares["b"].amount == 135000 and shares["b"].base == 150000


# --- eşleştirme -------------------------------------------------------------------------------

JAN, FEB, MAR = date(2026, 1, 15), date(2026, 2, 15), date(2026, 3, 15)


def test_fifo_then_priority():
    items = [
        OpenItem("mart-aidat", MAR, 10, 1500),
        OpenItem("ocak-yakit", JAN, 20, 800),
        OpenItem("ocak-aidat", JAN, 10, 1500),
    ]
    result = allocate(2000, items)
    assert result.lines == [("ocak-aidat", 1500), ("ocak-yakit", 500)]
    assert result.leftover == 0


def test_preferred_item_first():
    items = [OpenItem("ocak", JAN, 10, 1500), OpenItem("subat-yakit", FEB, 20, 800)]
    assert allocate(1000, items, preferred={"subat-yakit"}).lines == [("subat-yakit", 800), ("ocak", 200)]


def test_overpayment_becomes_advance():
    result = allocate(5000, [OpenItem("ocak", JAN, 10, 1500)])
    assert result.lines == [("ocak", 1500)] and result.leftover == 3500 and result.allocated == 1500


def test_payment_with_no_debt_is_all_advance():
    assert allocate(1000, []).leftover == 1000


def test_allocation_rejects_negative():
    with pytest.raises(AllocationError):
        allocate(-1, [])


items_strategy = st.lists(
    st.builds(
        OpenItem,
        id=st.uuids(),
        due_date=st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 1, 1)),
        priority=st.integers(min_value=0, max_value=100),
        remaining=st.integers(min_value=0, max_value=10**7),
    ),
    max_size=40,
)


@given(st.integers(min_value=0, max_value=10**8), items_strategy)
def test_allocation_properties(amount, items):
    result = allocate(amount, items)
    assert result.allocated + result.leftover == amount
    by_id = {i.id: i for i in items}
    assert all(0 < take <= by_id[item_id].remaining for item_id, take in result.lines)
    if result.leftover > 0:  # avans kaldıysa tüm borçlar kapanmıştır
        assert result.allocated == sum(i.remaining for i in items)
    # yalnızca son satır kısmi olabilir; öncekiler borcu tamamen kapatır
    for item_id, take in result.lines[:-1]:
        assert take == by_id[item_id].remaining


def test_bill_remaining_and_payment_check():
    from domain.bills import BillError, check_payment, remaining

    assert remaining(1000, [300, 200]) == 500
    assert check_payment(1000, [300], 700) == 0
    for bad in (0, -1, 701):
        with pytest.raises(BillError):
            check_payment(1000, [300], bad)
    with pytest.raises(BillError):
        remaining(1000, [1200])
