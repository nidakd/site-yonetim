"""Çekirdek muhasebe: defter güvenceleri, tahakkuk, tahsilat, avans, iptal, gider. Tüm veriler sentetik."""

from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction

from apps.billing import services as billing
from apps.billing.models import Charge, ChargeBatch, ChargeType, DuesAgreement
from apps.core.context import tenant_context
from apps.expenses import services as expenses
from apps.ledger import services as ledger
from apps.ledger.models import Account, JournalEntry, JournalLine
from apps.payments import services as payments
from apps.payments.models import Payment
from apps.people.models import Occupancy, Person
from apps.property.models import Block, Unit
from apps.reports.services import debtors, site_summary, unit_balances, unit_statement

from .conftest import make_site

NOV, DEC = date(2026, 11, 1), date(2026, 12, 1)


@pytest.fixture
def ctx(org_a):
    with tenant_context(org_a.pk):
        yield


@pytest.fixture
def site(org_a, ctx):
    site = make_site(org_a, "Çınar Evleri")
    billing.setup_site(site)
    block = Block.objects.create(site=site, name="A Blok")
    for no, (num, den, area) in enumerate(
        [(120, 1000, 950), (80, 1000, 700), (50, 1000, 450), (750, 1000, 1200)], start=1
    ):
        Unit.objects.create(
            site=site,
            block=block,
            number=str(no),
            share_numerator=num,
            share_denominator=den,
            gross_area=Decimal(area) / 10,
        )
    return site


def units(site):
    return list(Unit.objects.filter(site=site).order_by("sort_key"))


def aidat(site):
    return ChargeType.objects.get(site=site, name="Aidat")


def make_batch(site, *, method=ChargeBatch.Method.PER_UNIT, amount=150000, period=NOV, charge_type=None, **kw):
    return ChargeBatch.objects.create(
        site=site,
        charge_type=charge_type or aidat(site),
        period=period,
        due_date=period.replace(day=10),
        method=method,
        amount=amount,
        description=f"{period:%m.%Y} aidatı",
        **kw,
    )


def bank(site):
    return Account.objects.get(site=site, code="102.01")


def receivable_balance(site):
    return ledger.balance(ledger.system_account(site, Account.Role.RECEIVABLES))


def advance_balance(site):
    return -ledger.balance(ledger.system_account(site, Account.Role.ADVANCES))


def check_consistency(site):
    """Belgeler (borç/ödeme/eşleşme) ile defter her bölümde aynı bakiyeyi söylemeli."""
    balances = unit_balances(site)
    for unit in units(site):
        open_total = sum(c.remaining for c in billing.open_charges(unit))
        assert balances.get(unit.pk, 0) == open_total - payments.advance_balance(unit), unit.label
    with connection.cursor() as cur:
        cur.execute("SELECT coalesce(sum(debit) - sum(credit), 0) FROM ledger_journalline")
        assert cur.fetchone()[0] == 0  # tüm defter dengede


# --- defter -----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_setup_site_is_idempotent(site):
    count = Account.objects.filter(site=site).count()
    billing.setup_site(site)
    assert Account.objects.filter(site=site).count() == count
    assert set(ChargeType.objects.filter(site=site).values_list("name", flat=True)) == {
        "Aidat",
        "Yakıt",
        "Özel toplama",
        "Diğer",
    }


@pytest.mark.django_db
def test_post_rejects_unbalanced(site):
    with pytest.raises(ledger.LedgerError, match="dengesiz"):
        ledger.post(site, NOV, "x", [ledger.Line(bank(site), debit=100), ledger.Line(bank(site), credit=99)])


@pytest.mark.django_db
def test_database_rejects_unbalanced_entry(site):
    entry = ledger.post(site, NOV, "x", [ledger.Line(bank(site), debit=100), ledger.Line(bank(site), credit=100)])
    with pytest.raises(Exception, match="dengesiz"), transaction.atomic(), connection.cursor() as cur:
        cur.execute(
            "INSERT INTO ledger_journalline (organization_id, entry_id, account_id, debit, credit, memo, created_at, "
            "updated_at) VALUES (%s, %s, %s, 5, 0, '', now(), now())",
            [site.organization_id, entry.pk, bank(site).pk],
        )
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.django_db
def test_database_rejects_entry_without_lines(site):
    with pytest.raises(Exception, match="en az iki"), transaction.atomic():
        JournalEntry.objects.create(site=site, date=NOV, description="boş")
        with connection.cursor() as cur:
            cur.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.django_db
def test_ledger_rows_are_immutable(site):
    entry = ledger.post(site, NOV, "x", [ledger.Line(bank(site), debit=100), ledger.Line(bank(site), credit=100)])
    for sql in (
        "UPDATE ledger_journalline SET debit = 1 WHERE entry_id = %s",
        "DELETE FROM ledger_journalline WHERE entry_id = %s",
        "UPDATE ledger_journalentry SET description = 'x' WHERE id = %s",
        "DELETE FROM ledger_journalentry WHERE id = %s",
    ):
        with pytest.raises(Exception, match="değiştirilemez"), transaction.atomic(), connection.cursor() as cur:
            cur.execute(sql, [entry.pk])


@pytest.mark.django_db
def test_period_lock(site, admin_a):
    ledger.lock_period(site, date(2026, 11, 30), admin_a)
    with pytest.raises(ledger.PeriodLocked):
        ledger.post(site, NOV, "x", [ledger.Line(bank(site), debit=1), ledger.Line(bank(site), credit=1)])
    with pytest.raises(Exception, match="kilitli"), transaction.atomic():  # servis atlansa da veritabanı engeller
        JournalEntry.objects.create(site=site, date=NOV, description="arka kapı")
    with pytest.raises(ledger.LedgerError, match="ileri"):
        ledger.lock_period(site, date(2026, 10, 31), admin_a)
    ledger.post(site, DEC, "x", [ledger.Line(bank(site), debit=1), ledger.Line(bank(site), credit=1)])


@pytest.mark.django_db
def test_reverse_only_once(site):
    entry = ledger.post(site, NOV, "x", [ledger.Line(bank(site), debit=100), ledger.Line(bank(site), credit=100)])
    ledger.reverse(entry, NOV)
    with pytest.raises(ledger.LedgerError, match="zaten"):
        ledger.reverse(entry, NOV)


@pytest.mark.django_db
def test_other_org_cannot_see_ledger(site, org_b):
    ledger.post(site, NOV, "x", [ledger.Line(bank(site), debit=100), ledger.Line(bank(site), credit=100)])
    with tenant_context(org_b.pk):
        assert JournalLine.objects.count() == 0 and JournalLine.unscoped.count() == 0
        assert Charge.unscoped.count() == 0 and Account.unscoped.count() == 0


# --- tahakkuk ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_per_unit_batch_posts_balanced_entry(site, admin_a):
    batch = billing.approve(make_batch(site), admin_a)
    assert batch.status == "approved" and batch.total == 600000 and batch.unit_count == 4
    assert Charge.objects.filter(batch=batch).count() == 4
    assert receivable_balance(site) == 600000
    assert ledger.balance(aidat(site).income_account) == -600000
    check_consistency(site)


@pytest.mark.django_db
def test_share_batch_splits_total_exactly(site, admin_a):
    batch = billing.approve(make_batch(site, method=ChargeBatch.Method.SHARE, amount=100001), admin_a)
    amounts = [c.amount for c in Charge.objects.filter(batch=batch).order_by("unit__sort_key")]
    assert sum(amounts) == 100001
    assert amounts == [12000, 8000, 5000, 75001]


@pytest.mark.django_db
def test_missing_share_blocks_approval(site, admin_a):
    Unit.objects.create(site=site, number="99")
    batch = make_batch(site, method=ChargeBatch.Method.SHARE, amount=100000)
    assert "Arsa payı girilmemiş" in billing.compute_preview(batch).errors[0]
    with pytest.raises(billing.BillingError):
        billing.approve(batch, admin_a)
    assert not JournalEntry.objects.exists()


@pytest.mark.django_db
def test_agreements_apply_by_date(site, admin_a):
    u1, u2, u3, u4 = units(site)
    DuesAgreement.objects.create(unit=u1, kind="exempt", valid_from=date(2026, 1, 1))
    DuesAgreement.objects.create(unit=u2, kind="fixed", amount=50000, valid_from=date(2026, 1, 1))
    DuesAgreement.objects.create(
        unit=u3,
        kind="discount",
        discount_percent=Decimal("10"),
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    )
    DuesAgreement.objects.create(unit=u4, kind="exempt", valid_from=date(2025, 1, 1), valid_to=date(2025, 12, 31))
    batch = billing.approve(make_batch(site), admin_a)
    got = {c.unit_id: (c.base_amount, c.amount) for c in Charge.objects.filter(batch=batch)}
    assert u1.pk not in got  # muaf: borç kalemi oluşmaz
    assert got[u2.pk] == (150000, 50000)
    assert got[u3.pk] == (150000, 135000)
    assert got[u4.pk] == (150000, 150000)  # süresi dolmuş muafiyet uygulanmaz


@pytest.mark.django_db
def test_exempt_unit_leaves_denominator_for_total_methods(site, admin_a):
    u1 = units(site)[0]
    DuesAgreement.objects.create(unit=u1, kind="exempt", valid_from=date(2026, 1, 1))
    batch = billing.approve(make_batch(site, method=ChargeBatch.Method.EQUAL, amount=90000), admin_a)
    assert batch.total == 90000 and set(Charge.objects.filter(batch=batch).values_list("amount", flat=True)) == {30000}


@pytest.mark.django_db
def test_agreements_ignored_for_types_without_them(site, admin_a):
    DuesAgreement.objects.create(unit=units(site)[0], kind="exempt", valid_from=date(2026, 1, 1))
    fuel = ChargeType.objects.get(site=site, name="Yakıt")
    batch = billing.approve(make_batch(site, charge_type=fuel, amount=40000), admin_a)
    assert batch.unit_count == 4


@pytest.mark.django_db
def test_same_period_cannot_be_charged_twice(site, admin_a):
    billing.approve(make_batch(site), admin_a)
    second = make_batch(site)
    preview = billing.compute_preview(second)
    assert all(r.skipped for r in preview.rows) and preview.errors
    with pytest.raises(billing.BillingError):
        billing.approve(second, admin_a)
    with pytest.raises(IntegrityError), transaction.atomic():  # veritabanı da engeller
        first = Charge.objects.first()
        first.pk = None
        first.save()


@pytest.mark.django_db
def test_block_scope_then_rest_of_site_charges_only_missing(site, admin_a):
    block_b = Block.objects.create(site=site, name="B Blok")
    Unit.objects.create(site=site, block=block_b, number="1")
    billing.approve(make_batch(site, block=block_b), admin_a)
    batch = billing.approve(make_batch(site), admin_a)
    assert batch.unit_count == 4  # B Blok 1 zaten borçlandırılmıştı
    assert Charge.objects.filter(period=NOV).count() == 5


@pytest.mark.django_db
def test_archived_units_are_not_charged(site, admin_a):
    unit = units(site)[0]
    unit.is_archived = True
    unit.save()
    assert billing.approve(make_batch(site), admin_a).unit_count == 3


@pytest.mark.django_db
def test_responsible_snapshot_uses_period_start(site, admin_a):
    unit = units(site)[0]
    old = Person.objects.create(first_name="Eski", last_name="Malik")
    new = Person.objects.create(first_name="Yeni", last_name="Malik")
    Occupancy.objects.create(
        unit=unit, person=old, relation="owner", start_date=date(2020, 1, 1), end_date=date(2026, 11, 14)
    )
    Occupancy.objects.create(unit=unit, person=new, relation="owner", start_date=date(2026, 11, 15))
    billing.approve(make_batch(site), admin_a)
    billing.approve(make_batch(site, period=DEC), admin_a)
    assert Charge.objects.get(unit=unit, period=NOV).responsible[0]["name"] == "Eski Malik"
    assert Charge.objects.get(unit=unit, period=DEC).responsible[0]["name"] == "Yeni Malik"


# --- tahsilat ---------------------------------------------------------------------------------


@pytest.mark.django_db
def test_payment_fifo_priority_and_advance(site, admin_a):
    unit = units(site)[0]
    fuel = ChargeType.objects.get(site=site, name="Yakıt")
    billing.approve(make_batch(site, charge_type=fuel, amount=40000), admin_a)  # yakıt: öncelik 20
    billing.approve(make_batch(site), admin_a)  # aidat: öncelik 10, aynı vade
    billing.approve(make_batch(site, period=DEC), admin_a)

    p = payments.create_payment(
        site, unit, amount=170000, day=date(2026, 11, 20), channel="transfer", account=bank(site), user=admin_a
    )
    allocs = {a.charge.charge_type.name + a.charge.period.strftime("%m"): a.amount for a in p.allocations.all()}
    assert allocs == {"Aidat11": 150000, "Yakıt11": 20000}  # aynı vadede önce aidat
    p2 = payments.create_payment(
        site,
        unit,
        amount=200000,
        day=date(2026, 11, 21),
        channel="cash",
        account=Account.objects.get(site=site, code="100"),
        user=admin_a,
    )
    assert payments.unallocated(p2) == 30000  # yakıt 20.000 + aralık 150.000, kalan avans
    assert advance_balance(site) == 30000
    assert (p.receipt_no, p2.receipt_no) == ("2026-000001", "2026-000002")
    check_consistency(site)


@pytest.mark.django_db
def test_preferred_charge_is_paid_first(site, admin_a):
    unit = units(site)[0]
    billing.approve(make_batch(site), admin_a)
    billing.approve(make_batch(site, period=DEC), admin_a)
    dec_charge = Charge.objects.get(unit=unit, period=DEC)
    p = payments.create_payment(
        site,
        unit,
        amount=150000,
        day=date(2026, 11, 20),
        channel="cash",
        account=bank(site),
        user=admin_a,
        preferred=[dec_charge.pk],
    )
    assert [a.charge_id for a in p.allocations.all()] == [dec_charge.pk]


@pytest.mark.django_db
def test_advance_is_applied_when_new_charges_arrive(site, admin_a):
    unit = units(site)[0]
    p = payments.create_payment(
        site, unit, amount=200000, day=date(2026, 10, 25), channel="transfer", account=bank(site), user=admin_a
    )
    assert payments.unallocated(p) == 200000 and unit_balances(site)[unit.pk] == -200000
    billing.approve(make_batch(site), admin_a)
    assert payments.unallocated(p) == 50000
    assert billing.open_charges(unit) == []
    assert advance_balance(site) == 50000 and unit_balances(site)[unit.pk] == -50000
    check_consistency(site)


@pytest.mark.django_db
def test_cancel_payment_reopens_charges(site, admin_a):
    unit = units(site)[0]
    billing.approve(make_batch(site), admin_a)
    p = payments.create_payment(
        site, unit, amount=180000, day=date(2026, 11, 20), channel="transfer", account=bank(site), user=admin_a
    )
    payments.cancel_payment(p, admin_a, "Geri dönen EFT", day=date(2026, 11, 22))
    p.refresh_from_db()
    assert p.status == "cancelled" and p.receipt_number == 1
    assert sum(c.remaining for c in billing.open_charges(unit)) == 150000
    assert ledger.balance(bank(site)) == 0 and advance_balance(site) == 0
    with pytest.raises(payments.PaymentError):
        payments.cancel_payment(p, admin_a, "tekrar")
    check_consistency(site)


@pytest.mark.django_db
def test_cancel_batch_returns_payments_to_advance(site, admin_a):
    unit = units(site)[0]
    batch = billing.approve(make_batch(site), admin_a)
    p = payments.create_payment(
        site, unit, amount=100000, day=date(2026, 11, 20), channel="transfer", account=bank(site), user=admin_a
    )
    billing.cancel(batch, admin_a, "Yanlış tutar", day=date(2026, 11, 21))
    assert receivable_balance(site) == 0 and advance_balance(site) == 100000
    assert payments.unallocated(p) == 100000
    assert JournalEntry.objects.filter(reverses=batch.entry).exists()
    check_consistency(site)
    # doğru tahakkuk yeniden yapılabilir ve avans otomatik mahsup edilir
    billing.approve(make_batch(site, amount=120000), admin_a)
    assert payments.unallocated(p) == 0
    assert sum(c.remaining for c in billing.open_charges(unit)) == 20000
    check_consistency(site)


@pytest.mark.django_db
def test_payment_into_locked_period_is_refused(site, admin_a):
    ledger.lock_period(site, date(2026, 11, 30), admin_a)
    with pytest.raises(payments.PaymentError, match="kilitli"):
        payments.create_payment(
            site, units(site)[0], amount=100, day=date(2026, 11, 20), channel="cash", account=bank(site), user=admin_a
        )
    assert not Payment.objects.exists()


@pytest.mark.django_db
def test_payment_rejects_non_cash_account(site, admin_a):
    with pytest.raises(payments.PaymentError):
        payments.create_payment(
            site, units(site)[0], amount=100, day=NOV, channel="cash", account=aidat(site).income_account, user=admin_a
        )


# --- gider, virman, raporlar ------------------------------------------------------------------


@pytest.mark.django_db
def test_expense_and_cancel(site, admin_a):
    payments.create_payment(
        site, units(site)[0], amount=500000, day=NOV, channel="transfer", account=bank(site), user=admin_a
    )
    category = Account.objects.get(site=site, code="770.01")
    e = expenses.create_expense(
        site,
        day=date(2026, 11, 5),
        category=category,
        paid_from=bank(site),
        amount=123456,
        description="Ekim elektrik faturası",
        user=admin_a,
    )
    assert ledger.balance(bank(site)) == 500000 - 123456 and ledger.balance(category) == 123456
    expenses.cancel_expense(e, admin_a, "Mükerrer giriş", day=date(2026, 11, 6))
    assert ledger.balance(bank(site)) == 500000 and ledger.balance(category) == 0


@pytest.mark.django_db
def test_transfer_and_new_accounts(site, admin_a):
    second_bank = expenses.create_account(site, name="Vakıf Katılım", group="bank", iban="TR00 0000 0000")
    assert second_bank.code == "102.02" and second_bank.iban == "TR0000000000"
    assert expenses.create_account(site, name="Asansör bakımı", group="expense").code == "770.13"
    expenses.create_transfer(site, day=NOV, from_account=bank(site), to_account=second_bank, amount=1000, user=admin_a)
    assert ledger.balance(second_bank) == 1000 and ledger.balance(bank(site)) == -1000
    with pytest.raises(expenses.ExpenseError):
        expenses.create_transfer(site, day=NOV, from_account=bank(site), to_account=bank(site), amount=1, user=admin_a)


@pytest.mark.django_db
def test_statement_debtors_and_summary(site, admin_a):
    u1, u2, *_ = units(site)
    billing.approve(make_batch(site), admin_a)
    payments.create_payment(
        site,
        u1,
        amount=100000,
        day=date(2026, 11, 12),
        channel="cash",
        account=Account.objects.get(site=site, code="100"),
        user=admin_a,
    )
    rows = unit_statement(u1)
    assert [(r.debit, r.credit, r.balance) for r in rows] == [(150000, 0, 150000), (0, 100000, 50000)]
    late = debtors(site, as_of=date(2026, 11, 20))
    assert late[0]["days_late"] == 10 and {r["unit"].pk: r["balance"] for r in late}[u2.pk] == 150000
    summary = site_summary(site, today=date(2026, 11, 20))
    assert summary["accrued"] == 600000 and summary["collected"] == 100000
    assert summary["receivable"] == 500000 and summary["cash_total"] == 100000 and summary["debtor_count"] == 4


@pytest.mark.django_db
def test_chart_completed_for_old_sites(site):
    Account.objects.filter(site=site, code__in=["770.10", "770.11", "770.12"]).delete()
    Account.objects.filter(site=site, role=Account.Role.PAYABLES).delete()
    Account.objects.filter(site=site, code="770.04").update(name="Temizlik")
    Account.objects.create(site=site, code="770.10", name="Asansör bakımı", kind=Account.Kind.EXPENSE)
    ledger.ensure_chart(site)
    names = dict(Account.objects.filter(site=site, kind=Account.Kind.EXPENSE).values_list("name", "code"))
    assert names["Temizlik malzemesi"] == "770.11" and names["Yönetim ücreti"] == "770.13"
    assert "Temizlik hizmeti" not in names and names["Asansör bakımı"] == "770.10"
    assert ledger.system_account(site, Account.Role.PAYABLES).code == "320"
    count = Account.objects.filter(site=site).count()
    ledger.ensure_chart(site)
    assert Account.objects.filter(site=site).count() == count


@pytest.mark.django_db
def test_bill_partial_payments_and_cancel(site, admin_a):
    payables = ledger.system_account(site, Account.Role.PAYABLES)
    category = Account.objects.get(site=site, code="770.01")
    bill = expenses.create_bill(
        site,
        day=date(2026, 11, 3),
        due_date=date(2026, 11, 20),
        category=category,
        amount=100000,
        description="Kasım ortak alan elektriği",
        user=admin_a,
    )
    assert ledger.balance(category) == 100000 and ledger.balance(payables) == -100000
    assert list(expenses.open_bills(site)) == [bill]
    p1 = expenses.pay_bill(bill, day=date(2026, 11, 10), paid_from=bank(site), amount=40000, user=admin_a)
    with pytest.raises(expenses.ExpenseError):
        expenses.pay_bill(bill, day=date(2026, 11, 10), paid_from=bank(site), amount=60001, user=admin_a)
    with pytest.raises(expenses.ExpenseError):
        expenses.cancel_bill(bill, admin_a, "Yanlış")
    expenses.pay_bill(bill, day=date(2026, 11, 15), paid_from=bank(site), amount=60000, user=admin_a)
    bill.refresh_from_db()
    assert bill.remaining == 0 and not expenses.open_bills(site).exists()
    assert ledger.balance(payables) == 0 and ledger.balance(bank(site)) == -100000
    expenses.cancel_bill_payment(p1, admin_a, "Mükerrer", day=date(2026, 11, 16))
    bill.refresh_from_db()
    assert bill.remaining == 40000 and ledger.balance(payables) == -40000


@pytest.mark.django_db
def test_bill_cancel_without_payment(site, admin_a):
    category = Account.objects.get(site=site, code="770.06")
    bill = expenses.create_bill(
        site, day=NOV, category=category, amount=250000, description="Asansör bakımı", user=admin_a
    )
    expenses.cancel_bill(bill, admin_a, "Fatura geri alındı", day=NOV)
    assert ledger.balance(category) == 0
    with pytest.raises(expenses.ExpenseError):
        expenses.pay_bill(bill, day=NOV, paid_from=bank(site), amount=1, user=admin_a)
    with pytest.raises(expenses.ExpenseError):
        expenses.create_bill(
            site, day=NOV, due_date=date(2026, 10, 1), category=category, amount=1, description="x", user=admin_a
        )
