"""Duman testi: her ekran açılıyor, mobil menü ve form bileşenleri yerinde."""

import pytest
from django.urls import reverse

from .conftest import panel_client


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["accounts:login", "accounts:password_reset", "accounts:password_reset_done"])
def test_public_pages(client, name):
    response = client.get(reverse(name))
    assert response.status_code == 200
    assert 'lang="tr"' in response.content.decode()


@pytest.mark.django_db
def test_panel_pages(client, admin_a, org_a, site_a):
    panel_client(client, admin_a, org_a)
    for url in (
        reverse("tenancy:dashboard"),
        reverse("tenancy:site_create"),
        reverse("tenancy:site_detail", args=[site_a.pk]),
        reverse("tenancy:site_update", args=[site_a.pk]),
        reverse("audit:list"),
        reverse("accounts:security"),
    ):
        response = client.get(url)
        assert response.status_code == 200, url
        assert "Menüyü aç" in response.content.decode()


@pytest.mark.django_db
def test_custom_404(client, admin_a, org_a):
    panel_client(client, admin_a, org_a)
    response = client.get("/panel/siteler/999999/")
    assert response.status_code == 404 and "Sayfa bulunamadı" in response.content.decode()


@pytest.mark.django_db
def test_platform_admin_requires_staff_and_2fa(client, admin_a):
    from django.conf import settings

    panel_client(client, admin_a)
    assert client.get("/" + settings.ADMIN_URL).status_code == 302  # personel değil
    admin_a.is_staff = admin_a.is_superuser = True
    admin_a.save()
    assert client.get("/" + settings.ADMIN_URL + "tenancy/organization/").status_code == 200
