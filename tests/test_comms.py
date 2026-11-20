"""Faz 2: e-posta bildirimi (makbuz, ekstre) ve kişi anonimleştirme. Tüm veriler sentetik."""

import smtplib
from datetime import date

import pytest
from django.core import mail
from django.urls import reverse

from apps.billing import services as billing
from apps.billing.models import Charge, ChargeBatch, ChargeType
from apps.comms import services as comms
from apps.comms.models import Notification
from apps.core.context import tenant_context
from apps.ledger.models import Account
from apps.payments import services as payments
from apps.people import services as people
from apps.people.models import Occupancy, Person
from apps.property.models import Unit
from apps.residents.models import ResidentAccount
from apps.tenancy.models import Role

from .conftest import add_member, make_user, panel_client


@pytest.fixture
def world(org_a, site_a):
    with tenant_context(org_a.pk):
        billing.setup_site(site_a)
        unit = Unit.objects.create(site=site_a, number="1")
        owner = Person.objects.create(
            first_name="Deniz", last_name="Örnek", phone="05550000001", email="deniz@example.test"
        )
        tenant = Person.objects.create(
            first_name="Yaz", last_name="Kiracı", phone="05550000002", email="yaz@example.test"
        )
        quiet = Person.objects.create(first_name="Sessiz", last_name="Malik", email="sessiz@example.test")
        Occupancy.objects.create(
            unit=unit, person=owner, relation="owner", start_date=date(2026, 1, 1), is_liable=False
        )
        Occupancy.objects.create(unit=unit, person=tenant, relation="tenant", start_date=date(2026, 1, 1))
        Occupancy.objects.create(
            unit=unit, person=quiet, relation="owner", start_date=date(2026, 1, 1), receives_notifications=False
        )
        batch = ChargeBatch.objects.create(
            site=site_a,
            charge_type=ChargeType.objects.get(site=site_a, name="Aidat"),
            period=date(2026, 11, 1),
            due_date=date(2026, 11, 10),
            method="per_unit",
            amount=100000,
            description="Kasım aidatı",
        )
        billing.approve(batch, None)
    return {"site": site_a, "unit": unit, "owner": owner, "tenant": tenant, "quiet": quiet, "org": org_a}


def take_payment(client, world, **extra):
    data = {
        "unit": world["unit"].pk,
        "date": "2026-11-05",
        "amount": "1.200,00",
        "channel": "cash",
        "payer": "",
        "reference": "",
        "note": "",
    }
    with tenant_context(world["org"].pk):
        data["account"] = Account.objects.get(site=world["site"], code="100").pk
    data.update(extra)
    response = client.post(reverse("payments:payment_create", args=[world["site"].pk]), data)
    assert response.status_code == 302, response.content.decode()[:1500]
    return response


@pytest.mark.django_db
def test_receipt_email_goes_to_payer_only(client, admin_a, world):
    panel_client(client, admin_a, world["org"])
    take_payment(client, world, payer=world["tenant"].pk)
    assert [m.to for m in mail.outbox] == [["yaz@example.test"]]
    body = mail.outbox[0].body
    assert "Kasım aidatı" in body and "1.000,00 ₺" in body and "200,00 ₺" in body and "/sakin/giris/" in body
    with tenant_context(world["org"].pk):
        n = Notification.objects.get()
        assert n.kind == "receipt" and n.status == "sent" and n.person_id == world["tenant"].pk


@pytest.mark.django_db
def test_receipt_without_payer_goes_to_everyone_who_wants_it(client, admin_a, world):
    panel_client(client, admin_a, world["org"])
    take_payment(client, world)
    assert sorted(m.to[0] for m in mail.outbox) == ["deniz@example.test", "yaz@example.test"]


@pytest.mark.django_db
def test_email_failure_does_not_break_payment(client, admin_a, world, monkeypatch):
    def boom(*a, **k):
        raise smtplib.SMTPException("bağlantı yok")

    monkeypatch.setattr(comms, "send_mail", boom)
    panel_client(client, admin_a, world["org"])
    take_payment(client, world, payer=world["owner"].pk)
    with tenant_context(world["org"].pk):
        assert payments.Payment.objects.count() == 1
        n = Notification.objects.get()
        assert n.status == "failed" and n.error == "SMTPException"
    page = client.get(reverse("property:unit_detail", args=[world["site"].pk, world["unit"].pk])).content.decode()
    assert "gönderilemedi" in page


@pytest.mark.django_db
def test_statement_email(client, admin_a, world):
    panel_client(client, admin_a, world["org"])
    response = client.post(reverse("comms:send_statement", args=[world["site"].pk, world["unit"].pk]))
    assert response.status_code == 302
    assert len(mail.outbox) == 2
    assert "Kalan borç: 1.000,00 ₺" in mail.outbox[0].body and "Kasım aidatı" in mail.outbox[0].body
    page = client.get(reverse("property:unit_detail", args=[world["site"].pk, world["unit"].pk])).content.decode()
    assert "Hesap ekstresi · " in page


@pytest.mark.django_db
def test_statement_email_requires_permission(client, world):
    auditor = make_user("denetci@example.test")
    add_member(auditor, world["org"], Role.AUDITOR)
    panel_client(client, auditor, world["org"])
    assert client.post(reverse("comms:send_statement", args=[world["site"].pk, world["unit"].pk])).status_code == 403
    assert mail.outbox == []


@pytest.mark.django_db
def test_no_recipients_message(client, admin_a, world):
    with tenant_context(world["org"].pk):
        Person.objects.update(email="")
    panel_client(client, admin_a, world["org"])
    response = client.post(reverse("comms:send_statement", args=[world["site"].pk, world["unit"].pk]), follow=True)
    assert "E-postası kayıtlı" in response.content.decode()


# --- anonimleştirme ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_anonymize_keeps_ledger_and_receipt_but_removes_contact(world):
    with tenant_context(world["org"].pk):
        owner = world["owner"]
        cash = Account.objects.get(site=world["site"], code="100")
        pay = payments.create_payment(
            world["site"],
            world["unit"],
            amount=1000,
            day=date(2026, 11, 5),
            channel="cash",
            account=cash,
            user=None,
            payer=owner,
        )
        comms.send_receipt(pay)
        ResidentAccount.objects.create(phone=owner.phone)
        Occupancy.objects.filter(person=owner).update(is_liable=True)
        with pytest.raises(people.AnonymizeError):
            people.anonymize(owner)
        Occupancy.objects.filter(person=owner).update(end_date=date(2026, 11, 1))
        people.anonymize(owner)
        owner.refresh_from_db()
        assert (owner.first_name, owner.phone, owner.email, owner.is_anonymized) == ("Anonim", "", "", True)
        assert not ResidentAccount.objects.filter(phone="05550000001").exists()
        pay.refresh_from_db()
        assert pay.payer_name == "Deniz Örnek"  # makbuz belgesi korunur
        assert Notification.objects.get(person=owner).recipient == "—"
        for charge in Charge.objects.all():
            assert "Deniz" not in str(charge.responsible)


@pytest.mark.django_db
def test_anonymize_keeps_resident_account_if_phone_still_used(world):
    with tenant_context(world["org"].pk):
        Person.objects.create(first_name="Aile", last_name="Üyesi", phone="05550000001")
        ResidentAccount.objects.create(phone="05550000001")
        Occupancy.objects.filter(person=world["owner"]).update(end_date=date(2026, 11, 1))
        people.anonymize(world["owner"])
        assert ResidentAccount.objects.filter(phone="05550000001").exists()


@pytest.mark.django_db
def test_anonymize_screen(client, admin_a, world):
    panel_client(client, admin_a, world["org"])
    owner = world["owner"]
    url = reverse("people:person_detail", args=[world["site"].pk, owner.pk])
    assert "tüm bölüm ilişkilerini sonlandırın" in client.get(url).content.decode()
    with tenant_context(world["org"].pk):
        Occupancy.objects.filter(person=owner).update(end_date=date(2026, 11, 1))
    action = reverse("people:person_anonymize", args=[world["site"].pk, owner.pk])
    client.post(action)  # onay kutusu yok
    with tenant_context(world["org"].pk):
        assert not Person.objects.get(pk=owner.pk).is_anonymized
    client.post(action, {"confirm": "1"})
    page = client.get(url).content.decode()
    assert "anonimleştirilmiş" in page and "deniz@example.test" not in page
