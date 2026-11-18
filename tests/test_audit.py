import pytest
from django.core.management import call_command
from django.db import connection, transaction
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.audit.services import record
from apps.core.context import platform_context, tenant_context
from apps.tenancy.models import Membership, Organization, Role

from .conftest import add_member, make_user, panel_client


@pytest.mark.django_db
def test_events_cannot_be_updated_or_deleted_even_by_platform(org_a):
    with tenant_context(org_a.pk):
        event = record("site.created", organization_id=org_a.pk)
    with platform_context():
        for sql in ("UPDATE audit_auditevent SET action = 'x'", "DELETE FROM audit_auditevent"):
            with pytest.raises(Exception, match="değiştirilemez"), transaction.atomic(), connection.cursor() as cur:
                cur.execute(sql)
        assert AuditEvent.objects.get(pk=event.pk).action == "site.created"
    # TRUNCATE tetikleyicisi: test transaction'ı içinde çalıştırılamadığı için varlığı denetlenir
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_trigger WHERE tgname = 'audit_event_no_truncate'")
        assert cur.fetchone()[0] == 1


@pytest.mark.django_db
def test_unknown_action_is_rejected(org_a):
    with tenant_context(org_a.pk), pytest.raises(ValueError):
        record("bilinmeyen.islem")


@pytest.mark.django_db
def test_audit_list_shows_only_own_org(client, admin_a, org_a, org_b):
    with tenant_context(org_b.pk):
        record("site.created", changes={"name": "Gizli B"}, organization_id=org_b.pk)
    with tenant_context(org_a.pk):
        record("site.created", changes={"name": "Görünür A"}, organization_id=org_a.pk)
    panel_client(client, admin_a, org_a)
    page = client.get(reverse("audit:list")).content.decode()
    assert "Görünür A" in page and "Gizli B" not in page


@pytest.mark.django_db
@pytest.mark.parametrize(("role", "status"), [(Role.AUDITOR, 200), (Role.ACCOUNTANT, 403), (Role.SITE_ADMIN, 403)])
def test_audit_list_permissions(client, org_a, role, status):
    user = make_user(f"{role}@example.test")
    add_member(user, org_a, role)
    panel_client(client, user, org_a)
    assert client.get(reverse("audit:list")).status_code == status


@pytest.mark.django_db
def test_create_organization_command(client, capsys):
    call_command(
        "create_organization",
        name="Deneme Yönetim",
        admin_email="Ilk@Example.test",
        admin_name="İlk Yönetici",
        base_url="https://panel.example.test",
    )
    out = capsys.readouterr().out
    org = Organization.objects.get(name="Deneme Yönetim")
    membership = Membership.objects.get(organization=org)
    assert membership.role == Role.ORG_ADMIN and membership.user.email == "ilk@example.test"
    assert not membership.user.has_usable_password()
    link = next(line.strip() for line in out.splitlines() if line.strip().startswith("https://"))
    path = link.removeprefix("https://panel.example.test")
    assert client.get(path, follow=True).context["validlink"] is True
    with tenant_context(org.pk):
        assert set(AuditEvent.objects.values_list("action", flat=True)) == {"org.created", "member.added"}
