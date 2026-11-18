import pytest
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.core.context import tenant_context
from apps.tenancy.models import Role, Site

from .conftest import add_member, make_site, make_user, panel_client

DASH = reverse("tenancy:dashboard")


@pytest.mark.django_db
def test_org_admin_creates_site_and_it_is_audited(client, admin_a, org_a):
    panel_client(client, admin_a, org_a)
    response = client.post(reverse("tenancy:site_create"), {"name": "  Yeni   Site ", "city": "Ankara"})
    with tenant_context(org_a.pk):
        site = Site.objects.get()
        assert site.name == "Yeni Site" and site.organization_id == org_a.pk
        assert AuditEvent.objects.filter(action="site.created", target_id=str(site.pk)).exists()
    assert response.url == reverse("tenancy:site_detail", args=[site.pk])


@pytest.mark.django_db
def test_duplicate_site_name_is_rejected(client, admin_a, org_a, site_a):
    panel_client(client, admin_a, org_a)
    response = client.post(reverse("tenancy:site_create"), {"name": "a sitesi"})
    assert "Bu adla bir siteniz zaten var" in response.content.decode()


@pytest.mark.django_db
def test_same_site_name_is_allowed_in_another_org(client, admin_b, org_b, site_a):
    panel_client(client, admin_b, org_b)
    client.post(reverse("tenancy:site_create"), {"name": "A Sitesi"})
    with tenant_context(org_b.pk):
        assert Site.objects.filter(name="A Sitesi").exists()


@pytest.mark.django_db
def test_accountant_cannot_create_site(client, org_a):
    user = make_user("muhasebe@example.test")
    add_member(user, org_a, Role.ACCOUNTANT)
    panel_client(client, user, org_a)
    assert client.get(reverse("tenancy:site_create")).status_code == 403
    assert "Yeni site" not in client.get(DASH).content.decode()


@pytest.mark.django_db
def test_other_orgs_site_is_not_found(client, admin_a, org_a, site_b):
    panel_client(client, admin_a, org_a)
    assert client.get(reverse("tenancy:site_detail", args=[site_b.pk])).status_code == 404
    assert client.get(reverse("tenancy:site_update", args=[site_b.pk])).status_code == 404
    assert "B Sitesi" not in client.get(DASH).content.decode()


@pytest.mark.django_db
def test_switching_to_a_non_member_org_is_refused(client, admin_a, org_a, org_b):
    panel_client(client, admin_a, org_a)
    assert client.post(reverse("tenancy:switch_organization", args=[org_b.pk])).status_code == 404


@pytest.mark.django_db
def test_member_of_two_orgs_can_switch(client, admin_a, org_a, org_b, site_a, site_b):
    add_member(admin_a, org_b)
    panel_client(client, admin_a, org_a)
    assert "A Sitesi" in client.get(DASH).content.decode()
    client.post(reverse("tenancy:switch_organization", args=[org_b.pk]))
    page = client.get(DASH).content.decode()
    assert "B Sitesi" in page and "A Sitesi" not in page


@pytest.mark.django_db
def test_tampered_session_org_falls_back_to_own_org(client, admin_a, org_a, org_b, site_b):
    panel_client(client, admin_a, org_b)  # oturuma üye olmadığı kuruluş yazıldı
    page = client.get(DASH).content.decode()
    assert "Kuruluş A" in page and "B Sitesi" not in page


@pytest.mark.django_db
def test_site_scoped_user_sees_only_their_site(client, org_a, site_a):
    other = make_site(org_a, "Diğer Site")
    user = make_user("saha@example.test")
    add_member(user, org_a, Role.SITE_ADMIN, site=site_a)
    panel_client(client, user, org_a)
    page = client.get(DASH).content.decode()
    assert "A Sitesi" in page and "Diğer Site" not in page
    assert client.get(reverse("tenancy:site_detail", args=[other.pk])).status_code == 404
    assert client.get(reverse("tenancy:site_update", args=[site_a.pk])).status_code == 200


@pytest.mark.django_db
def test_site_update_records_changes(client, admin_a, org_a, site_a):
    panel_client(client, admin_a, org_a)
    client.post(reverse("tenancy:site_update", args=[site_a.pk]), {"name": "A Sitesi", "city": "İzmir"})
    with tenant_context(org_a.pk):
        event = AuditEvent.objects.get(action="site.updated")
    assert event.changes == {"city": {"eski": "İstanbul", "yeni": "İzmir"}}


@pytest.mark.django_db
def test_user_without_membership_sees_explanation(client):
    user = make_user("bos@example.test")
    panel_client(client, user)
    assert client.get(DASH).url == reverse("tenancy:no_organization")
    assert "bir kuruluşa bağlı değilsiniz" in client.get(reverse("tenancy:no_organization")).content.decode()


@pytest.mark.django_db
def test_inactive_membership_loses_access(client, admin_a, org_a):
    admin_a.memberships.update(is_active=False)
    panel_client(client, admin_a, org_a)
    assert client.get(DASH).url == reverse("tenancy:no_organization")
