"""Yeni düzen: gruplu yan menü, blokla birlikte daire açma, daireyle birlikte malik/kiracı, yeni kişi."""

from datetime import date

import pytest
from django.urls import reverse

from apps.billing import services as billing
from apps.core.context import tenant_context
from apps.people.models import Occupancy, Person
from apps.property.models import Block, Unit, UnitType
from apps.tenancy.models import Role

from .conftest import add_member, make_user, panel_client


@pytest.fixture
def site(org_a, site_a):
    with tenant_context(org_a.pk):
        billing.setup_site(site_a)
    return site_a


def url(name, site, *args):
    return reverse(name, args=[site.pk, *args])


@pytest.mark.django_db
def test_sidebar_groups_follow_permissions(client, admin_a, org_a, site):
    panel_client(client, admin_a, org_a)
    page = client.get(url("property:block_list", site)).content.decode()
    for text in ("Yönetim", "Muhasebe", "Ayarlar", "Borçlandırma", "Kasa ve banka", "Excel&#x27;den kurulum"):
        assert text in page, text
    assert 'aria-current="page"' in page

    staff = make_user("saha@example.test")
    add_member(staff, org_a, Role.FIELD_STAFF)
    panel_client(client, staff, org_a)
    page = client.get(url("property:block_list", site)).content.decode()
    assert "Daireler" in page and "Kişiler" in page
    assert "Borçlandırma" not in page and "Muhasebe ayarları" not in page and "Toplam borç" not in page


@pytest.mark.django_db
def test_block_with_units_in_one_go(client, admin_a, org_a, site):
    with tenant_context(org_a.pk):
        t = UnitType.objects.create(site=site, name="2+1", default_amount=100000)
    panel_client(client, admin_a, org_a)
    data = {
        "blok-name": "A Blok",
        "blok-unit_count": "10",
        "blok-floors": "3",
        "blok-first_number": "1",
        "blok-unit_type": t.pk,
    }
    response = client.post(url("property:block_list", site), data)
    assert response.status_code == 302
    with tenant_context(org_a.pk):
        units = list(Unit.objects.filter(site=site, block__name="A Blok").order_by("sort_key"))
        assert [u.number for u in units] == [str(n) for n in range(1, 11)]
        assert [u.floor for u in units] == [0, 0, 0, 0, 1, 1, 1, 1, 2, 2]
        assert all(u.unit_type_id == t.pk for u in units)
    page = client.get(url("property:block_list", site)).content.decode()
    assert "A Blok" in page and ">10<" in page
    # aynı ad tekrar eklenemez
    response = client.post(url("property:block_list", site), {"blok-name": "a blok"})
    assert response.status_code == 200 and "zaten var" in response.content.decode()


@pytest.mark.django_db
def test_block_only_without_units(client, admin_a, org_a, site):
    panel_client(client, admin_a, org_a)
    client.post(url("property:block_list", site), {"blok-name": "B Blok"})
    with tenant_context(org_a.pk):
        assert Block.objects.filter(site=site, name="B Blok").exists()
        assert not Unit.objects.filter(site=site).exists()


@pytest.mark.django_db
def test_unit_with_owner_and_tenant(client, admin_a, org_a, site):
    with tenant_context(org_a.pk):
        block = Block.objects.create(site=site, name="A Blok")
        existing = Person.objects.create(first_name="Deniz", last_name="Örnek", phone="05550000001")
    panel_client(client, admin_a, org_a)
    data = {
        "block": block.pk,
        "number": "5",
        "kind": "apartment",
        "owner_first": "deniz",
        "owner_last": "örnek",
        "owner_phone": "0555 000 00 01",
        "tenant_first": "Yaz",
        "tenant_last": "Kiracı",
        "tenant_phone": "+90 555 000 00 02",
        "tenant_email": "yaz@example.test",
        "start_date": "2026-11-01",
        "add_another": "1",
    }
    response = client.post(url("property:unit_create", site), data)
    assert response.status_code == 302 and response.url.endswith(f"?blok={block.pk}")
    with tenant_context(org_a.pk):
        unit = Unit.objects.get(site=site, number="5")
        occs = {o.relation: o for o in Occupancy.objects.filter(unit=unit)}
        assert occs["owner"].person_id == existing.pk  # aynı ad + telefon: mevcut kişi
        assert occs["tenant"].person.phone == "05550000002" and occs["tenant"].is_liable
        assert not occs["owner"].is_liable and occs["owner"].start_date == date(2026, 11, 1)


@pytest.mark.django_db
def test_unit_form_people_validation(client, admin_a, org_a, site):
    panel_client(client, admin_a, org_a)
    base = {"number": "1", "kind": "apartment"}
    page = client.post(url("property:unit_create", site), {**base, "owner_first": "Ali"}).content.decode()
    assert "ad ve soyadı birlikte" in page
    page = client.post(
        url("property:unit_create", site), {**base, "owner_first": "Ali", "owner_last": "Veli", "owner_phone": "123"}
    ).content.decode()
    assert "05xx" in page
    page = client.post(url("property:unit_create", site), {**base, "liable": "tenant"}).content.decode()
    assert "kiracı bilgisi yok" in page
    with tenant_context(org_a.pk):
        assert not Unit.objects.exists()


@pytest.mark.django_db
def test_person_create_with_and_without_unit(client, admin_a, org_a, site):
    with tenant_context(org_a.pk):
        unit = Unit.objects.create(site=site, number="1")
    panel_client(client, admin_a, org_a)
    response = client.post(
        url("people:person_create", site),
        {
            "first_name": "Ece",
            "last_name": "Örnek",
            "phone": "05550000003",
            "unit": unit.pk,
            "relation": "tenant",
            "start_date": "2026-11-01",
            "is_liable": "on",
        },
    )
    assert response.status_code == 302
    response = client.post(
        url("people:person_create", site), {"first_name": "Bağsız", "last_name": "Kişi", "relation": "owner"}
    )
    assert response.status_code == 302
    with tenant_context(org_a.pk):
        ece = Person.objects.get(first_name="Ece")
        assert Occupancy.objects.get(person=ece).unit_id == unit.pk
    page = client.get(url("people:person_list", site)).content.decode()
    assert "Ece Örnek" in page and "Bağsız Kişi" in page and "Daireye bağlı değil" in page


@pytest.mark.django_db
def test_setup_steps_on_empty_site(client, admin_a, org_a, site):
    panel_client(client, admin_a, org_a)
    page = client.get(url("tenancy:site_detail", site)).content.decode()
    assert "Siteyi kurmaya başlayın" in page and "Blok ekle" in page


@pytest.mark.django_db
def test_person_create_requires_permission(client, org_a, site):
    auditor = make_user("denetci@example.test")
    add_member(auditor, org_a, Role.AUDITOR)
    panel_client(client, auditor, org_a)
    assert client.get(url("people:person_create", site)).status_code == 403
    assert client.post(url("property:block_list", site), {"blok-name": "X"}).status_code == 403
