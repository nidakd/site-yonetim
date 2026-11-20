"""Faz 2: sakin girişi (telefon + SMS kodu), portal ve gizlilik. Tüm veriler sentetiktir."""

import re
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.billing import services as billing
from apps.billing.models import ChargeBatch, ChargeType
from apps.comms import sms
from apps.core.context import tenant_context
from apps.ledger.models import Account
from apps.payments import services as payments
from apps.people.models import Occupancy, Person
from apps.property.models import Block, Unit
from apps.residents import services
from apps.residents.models import OtpChallenge, PortalSettings, ResidentAccount
from apps.tenancy.models import Organization, Role

from .conftest import add_member, make_site, make_user, panel_client

OWNER, TENANT, FORMER = "05550000001", "05550000002", "05550000009"


@pytest.fixture(autouse=True)
def _outbox():
    sms.outbox.clear()
    yield
    sms.outbox.clear()


@pytest.fixture
def world(org_a, site_a):
    with tenant_context(org_a.pk):
        site = site_a
        billing.setup_site(site)
        block = Block.objects.create(site=site, name="A Blok")
        a1 = Unit.objects.create(site=site, block=block, number="1")
        a2 = Unit.objects.create(site=site, block=block, number="2")
        former = Person.objects.create(first_name="Eski", last_name="Malik", phone=FORMER)
        owner = Person.objects.create(first_name="Deniz", last_name="Örnek", phone=OWNER)
        tenant = Person.objects.create(first_name="Yaz", last_name="Kiracı", phone=TENANT)
        Occupancy.objects.create(
            unit=a1, person=former, relation="owner", start_date=date(2020, 1, 1), end_date=date(2026, 9, 30)
        )
        Occupancy.objects.create(unit=a1, person=owner, relation="owner", start_date=date(2026, 10, 1))
        Occupancy.objects.create(unit=a2, person=tenant, relation="tenant", start_date=date(2025, 1, 1))
        aidat = ChargeType.objects.get(site=site, name="Aidat")
        for period in (date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1)):
            batch = ChargeBatch.objects.create(
                site=site,
                charge_type=aidat,
                period=period,
                due_date=period.replace(day=10),
                method="per_unit",
                amount=100000,
                description=f"{period:%m.%Y} aidatı",
            )
            billing.approve(batch, None)
        cash = Account.objects.get(site=site, code="100")
        old = payments.create_payment(
            site, a1, amount=50000, day=date(2026, 9, 5), channel="cash", account=cash, user=None, payer=former
        )
        new = payments.create_payment(
            site, a1, amount=50000, day=date(2026, 10, 5), channel="cash", account=cash, user=None, payer=owner
        )
        other = payments.create_payment(
            site, a2, amount=100000, day=date(2026, 10, 6), channel="cash", account=cash, user=None
        )
    return {"site": site, "a1": a1, "a2": a2, "old": old, "new": new, "other": other, "org": org_a}


def last_code() -> str:
    return re.search(r"\b(\d{6})\b", sms.outbox[-1]["text"]).group(1)


def login(client, phone=OWNER, *, accept=True, remember=False):
    response = client.post(reverse("residents:login"), {"phone": phone})
    assert response.status_code == 302 and response.url == reverse("residents:login_code")
    data = {"code": last_code()}
    if remember:
        data["remember"] = "on"
    response = client.post(reverse("residents:login_code"), data)
    assert response.status_code == 302
    if accept:
        response = client.get(reverse("residents:home"))
        if response.status_code == 302 and response.url == reverse("residents:consent"):
            client.post(reverse("residents:consent"))
    return client


# --- giriş ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_unknown_phone_gets_same_screen_but_no_sms(client, world):
    response = client.post(reverse("residents:login"), {"phone": "0555 999 99 99"})
    assert response.status_code == 302 and response.url == reverse("residents:login_code")
    assert sms.outbox == [] and not OtpChallenge.objects.exists()


@pytest.mark.django_db
def test_former_owner_gets_no_code(client, world):
    client.post(reverse("residents:login"), {"phone": FORMER})
    assert sms.outbox == []


@pytest.mark.django_db
def test_landline_or_garbage_rejected(client, world):
    response = client.post(reverse("residents:login"), {"phone": "0216 123 45 67"})
    assert response.status_code == 200 and "Cep telefonunuzu" in response.content.decode()


@pytest.mark.django_db
def test_code_login_consent_and_home(client, world):
    client.post(reverse("residents:login"), {"phone": "+90 555 000 00 01"})
    assert len(sms.outbox) == 1 and sms.outbox[0]["phone"] == OWNER
    assert last_code() not in OtpChallenge.objects.get().code_hash  # kod düz yazılmaz
    client.post(reverse("residents:login_code"), {"code": last_code()})
    assert client.get(reverse("residents:home")).url == reverse("residents:consent")
    page = client.get(reverse("residents:consent")).content.decode()
    assert "Kuruluş A" in page and "KVKK" in page
    client.post(reverse("residents:consent"))
    page = client.get(reverse("residents:home")).content.decode()
    assert "A Blok · 1" in page and "A Blok · 2" not in page
    assert "Eski" not in page and "Yaz" not in page
    with tenant_context(world["org"].pk):
        from apps.audit.models import AuditEvent

        assert AuditEvent.objects.filter(action="resident.login").count() == 1
        assert AuditEvent.objects.filter(action="resident.consent").count() == 1
    # ikinci girişte onay tekrar sorulmaz
    client.post(reverse("residents:logout"))
    login(client, accept=False)
    assert client.get(reverse("residents:home")).status_code == 200


@pytest.mark.django_db
def test_wrong_code_attempts_are_limited(client, world):
    client.post(reverse("residents:login"), {"phone": OWNER})
    code = last_code()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        response = client.post(reverse("residents:login_code"), {"code": wrong})
        assert response.status_code == 200 and "hatalı" in response.content.decode()
    response = client.post(reverse("residents:login_code"), {"code": code})
    assert response.status_code == 200  # doğru kod da artık geçmez
    assert services.SESSION_KEY not in client.session


@pytest.mark.django_db
def test_expired_code_rejected(client, world):
    client.post(reverse("residents:login"), {"phone": OWNER})
    OtpChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    response = client.post(reverse("residents:login_code"), {"code": last_code()})
    assert response.status_code == 200


@pytest.mark.django_db
def test_new_code_invalidates_old(client, world):
    client.post(reverse("residents:login"), {"phone": OWNER})
    first = last_code()
    client.post(reverse("residents:login"), {"phone": OWNER})
    if first != last_code():
        assert client.post(reverse("residents:login_code"), {"code": first}).status_code == 200


@pytest.mark.django_db
def test_code_requests_are_throttled(client, world):
    for _ in range(3):
        client.post(reverse("residents:login"), {"phone": OWNER})
    response = client.post(reverse("residents:login"), {"phone": OWNER})
    assert response.status_code == 200 and "Çok fazla" in response.content.decode()
    assert len(sms.outbox) == 3


@pytest.mark.django_db
def test_remember_me_extends_session(client, world):
    login(client, remember=True)
    assert client.session.get_expiry_age() == 30 * 24 * 3600


@pytest.mark.django_db
def test_sms_backend_failure_is_reported(client, world, settings):
    settings.SMS_BACKEND = "apps.comms.sms.DisabledBackend"
    response = client.post(reverse("residents:login"), {"phone": OWNER})
    assert response.status_code == 200 and "gönderilemiyor" in response.content.decode()


# --- erişim kuralları -------------------------------------------------------------------------


@pytest.mark.django_db
def test_resident_sees_only_own_unit_and_own_period(client, world):
    login(client)
    a1, a2 = world["a1"], world["a2"]
    page = client.get(reverse("residents:unit_detail", args=[a1.pk])).content.decode()
    assert "Açık borçlar" in page
    assert "05.10.2026" in page and "05.09.2026" not in page  # eski malikin ödemesi görünmez
    assert client.get(reverse("residents:unit_detail", args=[a2.pk])).status_code == 404
    assert client.get(reverse("residents:receipt", args=[world["new"].pk])).status_code == 200
    assert client.get(reverse("residents:receipt", args=[world["old"].pk])).status_code == 404
    assert client.get(reverse("residents:receipt", args=[world["other"].pk])).status_code == 404


@pytest.mark.django_db
def test_statement_starts_with_carried_balance(client, world):
    login(client)
    page = client.get(reverse("residents:unit_detail", args=[world["a1"].pk])).content.decode()
    assert "Devreden bakiye" in page  # eski malikten kalan 500 TL, ödeme ayrıntısı olmadan


@pytest.mark.django_db
def test_access_ends_when_occupancy_ends(client, world):
    login(client)
    with tenant_context(world["org"].pk):
        Occupancy.objects.filter(person__phone=OWNER).update(end_date=date(2026, 10, 31))
    response = client.get(reverse("residents:home"))
    assert response.status_code == 302 and response.url == reverse("residents:login")
    assert services.SESSION_KEY not in client.session


@pytest.mark.django_db
def test_portal_disabled_blocks_login(client, world):
    with tenant_context(world["org"].pk):
        PortalSettings.objects.create(site=world["site"], portal_enabled=False)
    client.post(reverse("residents:login"), {"phone": OWNER})
    assert sms.outbox == []


@pytest.mark.django_db
def test_anonymised_person_has_no_access(client, world):
    with tenant_context(world["org"].pk):
        Person.objects.filter(phone=OWNER).update(is_anonymized=True)
    client.post(reverse("residents:login"), {"phone": OWNER})
    assert sms.outbox == []


@pytest.mark.django_db
def test_phone_in_two_organisations_must_choose(client, world):
    other_org = Organization.objects.create(name="Başka Yönetim")
    other_site = make_site(other_org, "Lale Sitesi")
    with tenant_context(other_org.pk):
        billing.setup_site(other_site)
        unit = Unit.objects.create(site=other_site, number="7")
        person = Person.objects.create(first_name="Deniz", last_name="Örnek", phone=OWNER)
        Occupancy.objects.create(unit=unit, person=person, relation="tenant", start_date=date(2026, 1, 1))
    login(client, accept=False)
    response = client.get(reverse("residents:home"))
    assert response.url == reverse("residents:choose_org")
    page = client.get(reverse("residents:choose_org")).content.decode()
    assert "Lale Sitesi" in page and "A Sitesi" in page
    client.post(reverse("residents:choose_org"), {"org": other_org.pk})
    client.post(reverse("residents:consent"))  # her veri sorumlusu için ayrı onay
    page = client.get(reverse("residents:home")).content.decode()
    assert "No 7" in page and "A Blok" not in page
    assert client.get(reverse("residents:unit_detail", args=[world["a1"].pk])).status_code == 404
    # başkasının kuruluşunu seçemez
    client.post(reverse("residents:choose_org"), {"org": 999999})
    assert client.session[services.SESSION_KEY]["org"] == other_org.pk


@pytest.mark.django_db
def test_portal_requires_login(client, world):
    for name, args in [("residents:home", []), ("residents:unit_detail", [world["a1"].pk])]:
        response = client.get(reverse(name, args=args))
        assert response.status_code == 302 and response.url == reverse("residents:login")


@pytest.mark.django_db
def test_resident_logout_keeps_admin_session(client, world, admin_a):
    panel_client(client, admin_a, world["org"])
    login(client)
    client.post(reverse("residents:logout"))
    assert client.get(reverse("tenancy:dashboard")).status_code == 200
    assert client.get(reverse("residents:home")).status_code == 302


# --- şifre ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_optional_password_login_and_lockout(client, world):
    login(client)
    response = client.post(
        reverse("residents:password"), {"password1": "Sakin-sifresi-42", "password2": "Sakin-sifresi-42"}
    )
    assert response.status_code == 302 and ResidentAccount.objects.get(phone=OWNER).has_password
    client.post(reverse("residents:logout"))
    response = client.post(reverse("residents:login_password"), {"phone": OWNER, "password": "Sakin-sifresi-42"})
    assert response.status_code == 302 and client.get(reverse("residents:home")).status_code == 200
    client.post(reverse("residents:logout"))
    for _ in range(5):
        client.post(reverse("residents:login_password"), {"phone": OWNER, "password": "yanlis"})
    response = client.post(reverse("residents:login_password"), {"phone": OWNER, "password": "Sakin-sifresi-42"})
    assert response.status_code == 200 and "Çok fazla" in response.content.decode()


@pytest.mark.django_db
def test_password_of_former_resident_does_not_open_portal(client, world):
    account = ResidentAccount.objects.create(phone=FORMER)
    account.set_password("Eski-sifre-12345")
    account.save()
    response = client.post(reverse("residents:login_password"), {"phone": FORMER, "password": "Eski-sifre-12345"})
    assert response.status_code == 200


# --- anonim özet ve ayarlar -------------------------------------------------------------------


@pytest.mark.django_db
def test_site_summary_shows_only_enabled_aggregates(client, world):
    login(client)
    site = world["site"]
    page = client.get(reverse("residents:site_summary", args=[site.pk])).content.decode()
    assert "tahsilat oranı" not in page and "Kasa ve banka" not in page
    with tenant_context(world["org"].pk):
        PortalSettings.objects.create(
            site=site, show_collection_rate=True, show_cash_balance=True, show_expenses=True, contact_note="Ofis 10-17"
        )
    page = client.get(reverse("residents:site_summary", args=[site.pk])).content.decode()
    assert "tahsilat oranı" in page and "Kasa ve banka toplamı" in page and "Ofis 10-17" in page
    assert "A Blok · 2" not in page and "Yaz" not in page


@pytest.mark.django_db
def test_site_overview_numbers(world):
    with tenant_context(world["org"].pk):
        ps = PortalSettings(site=world["site"], show_collection_rate=True, show_cash_balance=True)
        overview = services.site_overview(world["site"], ps, today=date(2026, 11, 20))
    # Kasım: 2 bölüm × 1.000 = 2.000 tahakkuk; A2'nin 1.000'lik ödemesi Eylül'ü kapatır → Kasım'a ödeme yok
    assert overview["collection"]["accrued"] == 200000
    assert overview["cash_total"] == 200000


@pytest.mark.django_db
def test_portal_settings_panel(client, world, admin_a):
    panel_client(client, admin_a, world["org"])
    url = reverse("residents_panel:portal_settings", args=[world["site"].pk])
    assert client.get(url).status_code == 200
    response = client.post(url, {"portal_enabled": "on", "show_cash_balance": "on", "contact_note": "Not"})
    assert response.status_code == 302
    with tenant_context(world["org"].pk):
        ps = PortalSettings.objects.get(site=world["site"])
        assert ps.show_cash_balance and not ps.show_expenses
    auditor = make_user("denetci@example.test")
    add_member(auditor, world["org"], Role.AUDITOR)
    panel_client(client, auditor, world["org"])
    assert client.get(url).status_code == 403
