"""Test yardımcıları. Tüm veriler sentetiktir.

Testler gerçek PostgreSQL üzerinde, süper kullanıcı OLMAYAN rolle çalışır; aksi halde RLS
devre dışı kalır ve izolasyon testleri anlamsızlaşır (bkz. test_rls.py::test_db_role...).
"""

import pytest
from django.core.cache import cache
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import User
from apps.core.context import tenant_context
from apps.tenancy.models import Membership, Organization, Role, Site

PASSWORD = "Test-sifresi-2026!"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def totp_now(device: TOTPDevice) -> str:
    return f"{TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift).token():06d}"


def make_user(email: str, *, with_2fa: bool = True, name: str = "Test Kullanıcı") -> User:
    user = User.objects.create_user(email, PASSWORD, full_name=name)
    if with_2fa:
        TOTPDevice.objects.create(user=user, name="test", confirmed=True)
    return user


def add_member(user, org, role=Role.ORG_ADMIN, site=None) -> Membership:
    return Membership.objects.create(user=user, organization=org, role=role, site=site)


def make_site(org, name: str) -> Site:
    with tenant_context(org.pk):
        return Site.objects.create(name=name, city="İstanbul")


def panel_client(client, user, org=None):
    """2FA doğrulanmış oturum."""
    client.force_login(user)
    device = TOTPDevice.objects.get(user=user, confirmed=True)
    session = client.session
    session["otp_device_id"] = device.persistent_id
    if org is not None:
        session["active_org_id"] = org.pk
    session.save()
    return client


@pytest.fixture
def org_a(db):
    return Organization.objects.create(name="Kuruluş A")


@pytest.fixture
def org_b(db):
    return Organization.objects.create(name="Kuruluş B")


@pytest.fixture
def admin_a(org_a):
    user = make_user("yonetici-a@example.test", name="Ayşe Yönetici")
    add_member(user, org_a)
    return user


@pytest.fixture
def admin_b(org_b):
    user = make_user("yonetici-b@example.test", name="Berk Yönetici")
    add_member(user, org_b)
    return user


@pytest.fixture
def site_a(org_a):
    return make_site(org_a, "A Sitesi")


@pytest.fixture
def site_b(org_b):
    return make_site(org_b, "B Sitesi")
