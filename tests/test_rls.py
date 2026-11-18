"""Kiracı izolasyonu: iki kuruluş birbirinin verisini ne ORM ile ne doğrudan SQL ile görebilir."""

import pytest
from django.apps import apps
from django.db import connection, transaction

from apps.audit.models import AuditEvent
from apps.core.context import TenantContextMissing, platform_context, tenant_context
from apps.tenancy.models import Site, TenantModel

RLS_TABLES_EXTRA = [AuditEvent]  # TenantModel'den türemeyen ama RLS'li tablolar


def raw_count(table: str) -> int:
    with connection.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


@pytest.mark.django_db
def test_db_role_cannot_bypass_rls():
    with connection.cursor() as cur:
        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        assert cur.fetchone() == (False, False), "Testler RLS'i atlayan bir rolle çalışıyor"


@pytest.mark.django_db
def test_every_tenant_table_has_forced_rls():
    models = [m for m in apps.get_models() if issubclass(m, TenantModel)] + RLS_TABLES_EXTRA
    assert Site in models
    with connection.cursor() as cur:
        for model in models:
            cur.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = %s", [model._meta.db_table]
            )
            assert cur.fetchone() == (True, True), f"{model._meta.db_table} tablosunda RLS açık değil"


@pytest.mark.django_db
def test_raw_sql_without_context_sees_nothing(site_a, site_b):
    assert raw_count("tenancy_site") == 0


@pytest.mark.django_db
def test_each_org_sees_only_its_own_rows(site_a, site_b):
    with tenant_context(site_a.organization_id):
        assert raw_count("tenancy_site") == 1
        assert list(Site.objects.values_list("name", flat=True)) == ["A Sitesi"]
        assert not Site.objects.filter(pk=site_b.pk).exists()
        assert not Site.unscoped.filter(pk=site_b.pk).exists()  # ORM filtresiz yönetici de: RLS engeller
    with tenant_context(site_b.organization_id):
        assert list(Site.objects.values_list("name", flat=True)) == ["B Sitesi"]


@pytest.mark.django_db
def test_database_rejects_insert_into_another_org(org_a, org_b):
    with tenant_context(org_a.pk), pytest.raises(Exception, match="row-level security"), transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO tenancy_site (organization_id, name, address, city, district, is_active, "
                "created_at, updated_at) VALUES (%s, 'Sızma', '', '', '', true, now(), now())",
                [org_b.pk],
            )


@pytest.mark.django_db
def test_database_rejects_update_of_another_orgs_row(site_a, site_b):
    with tenant_context(site_a.organization_id), connection.cursor() as cur:
        cur.execute("UPDATE tenancy_site SET name = 'değişti' WHERE id = %s", [site_b.pk])
        assert cur.rowcount == 0
    with tenant_context(site_b.organization_id):
        assert Site.objects.get(pk=site_b.pk).name == "B Sitesi"


@pytest.mark.django_db
def test_orm_without_context_fails_closed(site_a):
    with pytest.raises(TenantContextMissing):
        list(Site.objects.all())


@pytest.mark.django_db
def test_saving_into_another_org_is_refused(site_a, org_b):
    with tenant_context(org_b.pk), pytest.raises(PermissionError):
        Site(name="X", organization_id=site_a.organization_id).save()


@pytest.mark.django_db
def test_platform_context_sees_everything(site_a, site_b):
    with platform_context():
        assert raw_count("tenancy_site") == 2
    assert raw_count("tenancy_site") == 0  # bağlam kapanınca izolasyon geri gelir
