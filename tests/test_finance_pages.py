"""Faz 1 ekranları: her sayfa açılıyor, formlar çalışıyor, yetkiler ve kiracı sınırı korunuyor."""

from datetime import date
from io import StringIO

import pytest
from django.urls import reverse

from apps.billing import services as billing
from apps.billing.models import Charge, ChargeBatch, DuesAgreement
from apps.core.context import tenant_context
from apps.expenses.models import Expense, Transfer
from apps.ledger.models import Account, PeriodLock
from apps.payments.models import Payment
from apps.people.models import Occupancy, Person
from apps.property.models import Block, Unit
from apps.tenancy.models import Role

from .conftest import add_member, make_site, make_user, panel_client


@pytest.fixture
def setup(org_a, site_a):
    with tenant_context(org_a.pk):
        billing.setup_site(site_a)
        block = Block.objects.create(site=site_a, name="A Blok")
        unit = Unit.objects.create(site=site_a, block=block, number="1", share_numerator=1, share_denominator=2)
        Unit.objects.create(site=site_a, block=block, number="2", share_numerator=1, share_denominator=2)
        person = Person.objects.create(first_name="Deniz", last_name="Örnek", phone="05550000001")
        Occupancy.objects.create(unit=unit, person=person, relation="owner", start_date=date(2020, 1, 1))
    return {"site": site_a, "unit": unit, "block": block, "person": person}


def url(name, site, *args):
    return reverse(name, args=[site.pk, *args])


def approve_batch(client, site, **data):
    payload = {
        "charge_type": "",
        "period": "2026-11",
        "due_date": "2026-11-10",
        "method": "per_unit",
        "amount": "1.500,00",
        "scope": "site",
        "description": "",
        "decision_ref": "",
    }
    with tenant_context(site.organization_id):
        payload["charge_type"] = site.charge_types.get(name="Aidat").pk
    payload.update(data)
    response = client.post(url("billing:batch_create", site), payload)
    assert response.status_code == 302, response.content.decode()[:2000]
    batch_id = int(response.url.rstrip("/").split("/")[-1])
    response = client.post(url("billing:batch_approve", site, batch_id))
    assert response.status_code == 302
    return batch_id


@pytest.mark.django_db
def test_all_site_pages_open(client, admin_a, org_a, setup):
    site, unit, person = setup["site"], setup["unit"], setup["person"]
    panel_client(client, admin_a, org_a)
    batch_id = approve_batch(client, site)
    with tenant_context(org_a.pk):
        cash = Account.objects.get(site=site, code="100")
    client.post(
        url("payments:payment_create", site),
        {"unit": unit.pk, "date": "2026-11-12", "amount": "500", "channel": "cash", "account": cash.pk},
    )
    with tenant_context(org_a.pk):
        payment = Payment.objects.get()
    pages = [
        url("tenancy:site_detail", site),
        url("property:unit_list", site),
        url("property:unit_list", site) + "?q=Deniz",
        url("property:unit_create", site),
        url("property:unit_bulk_create", site),
        url("property:unit_detail", site, unit.pk),
        url("property:unit_update", site, unit.pk),
        url("property:structure", site),
        url("property:block_update", site, setup["block"].pk),
        url("people:person_list", site),
        url("people:person_detail", site, person.pk),
        url("people:person_update", site, person.pk),
        url("people:occupancy_create", site, unit.pk),
        url("billing:batch_list", site),
        url("billing:batch_create", site),
        url("billing:batch_detail", site, batch_id),
        url("billing:agreement_create", site, unit.pk),
        url("billing:charge_type_create", site),
        url("payments:payment_list", site),
        url("payments:payment_create", site) + f"?bolum={unit.pk}",
        url("payments:payment_detail", site, payment.pk),
        url("payments:receipt", site, payment.pk),
        url("expenses:expense_list", site),
        url("expenses:cash_bank", site),
        url("ledger:journal", site),
        url("ledger:trial_balance", site),
        url("ledger:settings", site),
        url("ledger:account_detail", site, cash.pk),
        url("reports:debtors", site),
        url("reports:unit_statement", site, unit.pk),
        url("reports:unit_statement", site, unit.pk) + "?baslangic=2026-11-11",
    ]
    for page in pages:
        response = client.get(page)
        assert response.status_code == 200, page
    assert "1.000,00 ₺" in client.get(url("reports:unit_statement", site, unit.pk)).content.decode()


@pytest.mark.django_db
def test_unit_forms(client, admin_a, org_a, setup):
    site, block = setup["site"], setup["block"]
    panel_client(client, admin_a, org_a)
    r = client.post(url("property:unit_create", site), {"block": block.pk, "number": "1", "kind": "apartment"})
    assert "aynı numaralı" in r.content.decode()
    r = client.post(
        url("property:unit_create", site),
        {"block": block.pk, "number": "3", "kind": "apartment", "share_numerator": "5"},
    )
    assert "hem payını hem paydasını" in r.content.decode()
    r = client.post(
        url("property:unit_bulk_create", site), {"block": block.pk, "first": 3, "last": 12, "kind": "apartment"}
    )
    assert r.status_code == 302
    with tenant_context(org_a.pk):
        assert Unit.objects.filter(site=site).count() == 12


@pytest.mark.django_db
def test_add_person_to_unit_and_end(client, admin_a, org_a, setup):
    site, unit = setup["site"], setup["unit"]
    panel_client(client, admin_a, org_a)
    r = client.post(
        url("people:occupancy_create", site, unit.pk),
        {
            "first_name": "Kiracı",
            "last_name": "Kişi",
            "phone": "+90 555 000 00 02",
            "relation": "tenant",
            "start_date": "2026-11-01",
            "is_liable": "on",
        },
    )
    assert r.status_code == 302
    with tenant_context(org_a.pk):
        occ = Occupancy.objects.get(relation="tenant")
        assert occ.person.phone == "05550000002"
    r = client.post(url("people:occupancy_end", site, occ.pk), {"end_date": "2026-10-01"})
    assert "başlangıçtan önce" in r.content.decode()
    client.post(url("people:occupancy_end", site, occ.pk), {"end_date": "2026-11-30"})
    with tenant_context(org_a.pk):
        assert Occupancy.objects.get(pk=occ.pk).end_date == date(2026, 11, 30)


@pytest.mark.django_db
def test_batch_wizard_preview_shows_errors(client, admin_a, org_a, setup):
    site = setup["site"]
    with tenant_context(org_a.pk):
        Unit.objects.create(site=site, number="9")  # arsa payı yok
    panel_client(client, admin_a, org_a)
    with tenant_context(org_a.pk):
        ct = site.charge_types.get(name="Aidat").pk
    r = client.post(
        url("billing:batch_create", site),
        {
            "charge_type": ct,
            "period": "2026-11",
            "due_date": "2026-11-10",
            "method": "share",
            "amount": "1000",
            "scope": "site",
        },
    )
    page = client.get(r.url).content.decode()
    assert "Arsa payı girilmemiş" in page and "Onayla ve borçlandır" not in page


@pytest.mark.django_db
def test_agreement_and_batch_cancel(client, admin_a, org_a, setup):
    site, unit = setup["site"], setup["unit"]
    panel_client(client, admin_a, org_a)
    r = client.post(
        url("billing:agreement_create", site, unit.pk),
        {"kind": "discount", "discount_percent": "50", "valid_from": "2026-01-01"},
    )
    assert r.status_code == 302
    r = client.post(url("billing:agreement_create", site, unit.pk), {"kind": "exempt", "valid_from": "2026-06-01"})
    assert "başka bir özel aidat" in r.content.decode()
    batch_id = approve_batch(client, site)
    with tenant_context(org_a.pk):
        assert Charge.objects.get(unit=unit).amount == 75000
        assert DuesAgreement.objects.count() == 1
    client.post(url("billing:batch_cancel", site, batch_id), {"reason": "Deneme"})
    with tenant_context(org_a.pk):
        assert ChargeBatch.objects.get(pk=batch_id).status == "cancelled"


@pytest.mark.django_db
def test_payment_flow_with_htmx_panel_and_cancel(client, admin_a, org_a, setup):
    site, unit = setup["site"], setup["unit"]
    panel_client(client, admin_a, org_a)
    approve_batch(client, site)
    panel = client.get(url("payments:payment_create", site) + f"?unit={unit.pk}", headers={"HX-Request": "true"})
    assert "Açık borç" in panel.content.decode() and "<html" not in panel.content.decode()
    with tenant_context(org_a.pk):
        bank = Account.objects.get(site=site, code="102.01")
        cash = Account.objects.get(site=site, code="100")
    r = client.post(
        url("payments:payment_create", site),
        {"unit": unit.pk, "date": "2026-11-12", "amount": "100", "channel": "cash", "account": bank.pk},
    )
    assert "Nakit tahsilat kasaya" in r.content.decode()
    r = client.post(
        url("payments:payment_create", site),
        {
            "unit": unit.pk,
            "date": "2026-11-12",
            "amount": "2.000,00",
            "channel": "transfer",
            "account": bank.pk,
            "payer": setup["person"].pk,
        },
    )
    assert r.status_code == 302
    with tenant_context(org_a.pk):
        payment = Payment.objects.get()
        assert payment.payer_name == "Deniz Örnek" and payment.receipt_no == "2026-000001"
    detail = client.get(r.url).content.decode()
    assert "500,00 ₺" in detail  # avansa kalan
    client.post(url("payments:payment_cancel", site, payment.pk), {"reason": "Geri dönen EFT"})
    assert "İPTAL" in client.get(url("payments:receipt", site, payment.pk)).content.decode()
    _ = cash


@pytest.mark.django_db
def test_expense_transfer_account_and_lock(client, admin_a, org_a, setup):
    site = setup["site"]
    panel_client(client, admin_a, org_a)
    with tenant_context(org_a.pk):
        cash = Account.objects.get(site=site, code="100")
        bank = Account.objects.get(site=site, code="102.01")
        elec = Account.objects.get(site=site, code="770.01")
    client.post(url("expenses:expense_list", site), {"ted-name": "Örnek Elektrik A.Ş."})
    r = client.post(
        url("expenses:expense_list", site),
        {
            "date": "2026-11-05",
            "category": elec.pk,
            "description": "Ekim elektrik",
            "amount": "1.234,56",
            "paid_from": bank.pk,
        },
    )
    assert r.status_code == 302
    client.post(
        url("expenses:cash_bank", site),
        {"vir-date": "2026-11-06", "vir-from_account": bank.pk, "vir-to_account": cash.pk, "vir-amount": "100"},
    )
    client.post(url("expenses:cash_bank", site), {"hes-group": "bank", "hes-name": "İkinci banka", "hes-iban": ""})
    with tenant_context(org_a.pk):
        expense = Expense.objects.get()
        assert expense.amount == 123456 and Transfer.objects.count() == 1
        assert Account.objects.filter(code="102.02").exists()
    client.post(url("expenses:expense_cancel", site, expense.pk), {"reason": "Mükerrer"})
    client.post(url("ledger:settings", site), {"locked_until": "2026-11-30"})
    with tenant_context(org_a.pk):
        assert Expense.objects.get().status == "cancelled"
        assert PeriodLock.objects.get(site=site).locked_until == date(2026, 11, 30)
    r = client.post(
        url("expenses:expense_list", site),
        {"date": "2026-11-20", "category": elec.pk, "description": "Geç fatura", "amount": "10", "paid_from": bank.pk},
    )
    assert "kilitli" in r.content.decode()


@pytest.mark.django_db
def test_field_staff_sees_units_but_not_finance(client, org_a, setup):
    site = setup["site"]
    user = make_user("saha@example.test")
    add_member(user, org_a, Role.FIELD_STAFF, site=site)
    panel_client(client, user, org_a)
    assert client.get(url("property:unit_list", site)).status_code == 200
    assert "Tahakkuk" not in client.get(url("property:unit_list", site)).content.decode()
    for name in (
        "billing:batch_list",
        "payments:payment_list",
        "expenses:cash_bank",
        "reports:debtors",
        "ledger:journal",
        "payments:payment_create",
        "property:unit_create",
    ):
        assert client.get(url(name, site)).status_code == 403, name


@pytest.mark.django_db
def test_auditor_reads_but_cannot_write(client, org_a, setup):
    site = setup["site"]
    user = make_user("denetci@example.test")
    add_member(user, org_a, Role.AUDITOR)
    panel_client(client, user, org_a)
    assert client.get(url("ledger:trial_balance", site)).status_code == 200
    assert client.get(url("billing:batch_create", site)).status_code == 403
    assert client.post(url("ledger:settings", site), {"locked_until": "2026-11-30"}).status_code == 403
    assert client.post(url("expenses:expense_list", site), {"ted-name": "X"}).status_code == 403


@pytest.mark.django_db
def test_site_scoped_accountant_cannot_open_other_site(client, org_a, setup):
    other = make_site(org_a, "Diğer Site")
    user = make_user("muhasebe2@example.test")
    add_member(user, org_a, Role.ACCOUNTANT, site=setup["site"])
    panel_client(client, user, org_a)
    assert client.get(url("billing:batch_list", setup["site"])).status_code == 200
    assert client.get(url("billing:batch_list", other)).status_code == 403


@pytest.mark.django_db
def test_other_org_cannot_reach_site_pages(client, admin_b, org_b, setup):
    site, unit = setup["site"], setup["unit"]
    panel_client(client, admin_b, org_b)
    for page in (
        url("property:unit_list", site),
        url("property:unit_detail", site, unit.pk),
        url("reports:unit_statement", site, unit.pk),
        url("payments:payment_create", site),
    ):
        assert client.get(page).status_code == 404, page


@pytest.mark.django_db
def test_unit_from_other_site_is_not_found(client, admin_a, org_a, setup):
    other = make_site(org_a, "Diğer Site")
    panel_client(client, admin_a, org_a)
    assert client.get(url("property:unit_detail", other, setup["unit"].pk)).status_code == 404


@pytest.mark.django_db
def test_seed_demo_builds_consistent_site():
    from django.core.management import call_command
    from django.db import connection

    from apps.core.context import platform_context
    from apps.payments.services import advance_balance
    from apps.reports.services import unit_balances
    from apps.tenancy.models import Organization, Site

    call_command("seed_demo", "--org-name", "Test Demo", "--admin-email", "seed@example.test", stdout=StringIO())
    with platform_context():
        org = Organization.objects.get(name="Test Demo")
    with tenant_context(org.pk):
        site = Site.objects.get()
        assert Unit.objects.filter(site=site).count() == 16
        balances = unit_balances(site)
        for unit in Unit.objects.filter(site=site):
            open_total = sum(c.remaining for c in billing.open_charges(unit))
            assert balances.get(unit.pk, 0) == open_total - advance_balance(unit)
        with connection.cursor() as cur:
            cur.execute("SELECT sum(debit) - sum(credit) FROM ledger_journalline")
            assert cur.fetchone()[0] == 0
