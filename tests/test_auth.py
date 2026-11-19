from datetime import timedelta

import pytest
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django_otp.plugins.otp_static.models import StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import TrustedDevice
from apps.accounts.services import device_label
from apps.audit.models import AuditEvent
from apps.core.context import platform_context

from .conftest import PASSWORD, add_member, make_user, totp_now

LOGIN, TWO_FA, DASH = reverse("accounts:login"), reverse("accounts:two_factor"), reverse("tenancy:dashboard")


def actions():
    with platform_context():
        return list(AuditEvent.objects.order_by("id").values_list("action", flat=True))


def post_login(client, email, password=PASSWORD, next_url=None):
    url = LOGIN + (f"?next={next_url}" if next_url else "")
    return client.post(url, {"email": email, "password": password})


@pytest.mark.django_db
def test_wrong_password_and_unknown_email_look_the_same(client, admin_a):
    wrong = post_login(client, admin_a.email, "yanlis-sifre")
    unknown = post_login(client, "kimse@example.test", "yanlis-sifre")
    assert wrong.status_code == unknown.status_code == 200
    assert "E-posta ya da şifre hatalı." in wrong.content.decode()
    assert "E-posta ya da şifre hatalı." in unknown.content.decode()
    assert actions() == ["auth.login_failed", "auth.login_failed"]


@pytest.mark.django_db
def test_lockout_after_five_failures_even_with_correct_password(client, admin_a):
    for _ in range(5):
        post_login(client, admin_a.email, "yanlis-sifre")
    response = post_login(client, admin_a.email)
    assert "Çok fazla hatalı deneme" in response.content.decode()
    assert "auth.login_locked" in actions()
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_password_alone_does_not_log_in_when_2fa_is_set(client, admin_a):
    response = post_login(client, admin_a.email)
    assert response.url == TWO_FA
    assert "_auth_user_id" not in client.session  # kod girilmeden oturum açılmaz
    assert client.get(DASH).status_code == 302


@pytest.mark.django_db
def test_correct_code_logs_in_and_respects_safe_next(client, admin_a, site_a):
    post_login(client, admin_a.email, next_url=reverse("tenancy:site_detail", args=[site_a.pk]))
    device = TOTPDevice.objects.get(user=admin_a)
    response = client.post(TWO_FA, {"token": totp_now(device)})
    assert response.url == reverse("tenancy:site_detail", args=[site_a.pk])
    assert client.get(DASH).status_code == 200
    assert actions()[-1] == "auth.login"


@pytest.mark.django_db
def test_open_redirect_is_blocked(client, admin_a):
    post_login(client, admin_a.email, next_url="https://kotu-site.example/")
    device = TOTPDevice.objects.get(user=admin_a)
    assert client.post(TWO_FA, {"token": totp_now(device)}).url == DASH


@pytest.mark.django_db
def test_five_wrong_codes_end_the_attempt(client, admin_a):
    post_login(client, admin_a.email)
    device = TOTPDevice.objects.get(user=admin_a)
    for _ in range(4):
        assert client.post(TWO_FA, {"token": "000000"}).status_code == 200
        device.refresh_from_db()
        device.throttle_reset()
    assert client.post(TWO_FA, {"token": "000000"}).url == LOGIN
    assert client.get(TWO_FA).url == LOGIN
    assert actions().count("auth.2fa_failed") == 5


@pytest.mark.django_db
def test_new_user_must_set_up_2fa_before_using_panel(client, org_a):
    user = make_user("yeni@example.test", with_2fa=False)
    add_member(user, org_a)
    assert post_login(client, user.email).url == TWO_FA
    assert client.get(DASH).url == TWO_FA  # oturum açık ama panel kilitli
    page = client.get(TWO_FA)
    assert "<svg" in page.content.decode()
    device = TOTPDevice.objects.get(user=user, confirmed=False)
    assert client.post(TWO_FA, {"token": "111111"}).status_code == 200  # hatalı kod
    too_fast = client.post(TWO_FA, {"token": totp_now(device)}).content.decode()
    assert "saniye sonra tekrar deneyin" in too_fast  # neden reddedildiği söyleniyor
    device.refresh_from_db()
    device.throttle_reset()  # bekleme süresi geçti
    response = client.post(TWO_FA, {"token": totp_now(device)})
    assert response.url == reverse("accounts:backup_codes")
    codes_page = client.get(response.url).content.decode()
    assert codes_page.count('class="rounded bg-gray-100') == 10
    assert client.get(reverse("accounts:backup_codes")).status_code == 302  # kodlar bir kez gösterilir
    assert client.get(DASH).status_code == 200
    assert "auth.2fa_enabled" in actions()


@pytest.mark.django_db
def test_backup_code_works_once(client, admin_a):
    from apps.accounts.services import regenerate_backup_codes

    code = regenerate_backup_codes(admin_a)[0]
    post_login(client, admin_a.email)
    assert client.post(TWO_FA, {"token": code}).url == DASH
    assert not StaticToken.objects.filter(token=code).exists()
    client.post(reverse("accounts:logout"))
    post_login(client, admin_a.email)
    assert client.post(TWO_FA, {"token": code}).status_code == 200  # ikinci kez çalışmaz


@pytest.mark.django_db
def test_logout_requires_post(client, admin_a):
    from .conftest import panel_client

    panel_client(client, admin_a)
    assert client.get(reverse("accounts:logout")).status_code == 405
    assert client.post(reverse("accounts:logout")).url == LOGIN
    assert client.get(DASH).status_code == 302


@pytest.mark.django_db
def test_password_reset_link_sets_new_password(client, admin_a):
    uid = urlsafe_base64_encode(force_bytes(admin_a.pk))
    token = default_token_generator.make_token(admin_a)
    url = client.get(reverse("accounts:password_reset_confirm", args=[uid, token])).url  # Django jetonu oturuma taşır
    response = client.post(url, {"new_password1": "Yeni-Guclu-Sifre-77", "new_password2": "Yeni-Guclu-Sifre-77"})
    assert response.url == reverse("accounts:password_reset_complete")
    admin_a.refresh_from_db()
    assert admin_a.check_password("Yeni-Guclu-Sifre-77")
    assert "auth.password_changed" in actions()


@pytest.mark.django_db
def test_password_reset_does_not_reveal_unknown_emails(client, admin_a, mailoutbox):
    for email in (admin_a.email, "kimse@example.test"):
        response = client.post(reverse("accounts:password_reset"), {"email": email})
        assert response.url == reverse("accounts:password_reset_done")
    assert len(mailoutbox) == 1


# ---------------------------------------------------------------- bu cihazı hatırla


def login_with_code(client, user, remember):
    post_login(client, user.email)
    device = TOTPDevice.objects.get(user=user)
    data = {"token": totp_now(device)}
    if remember:
        data["remember"] = "on"
    return client.post(TWO_FA, data)


def logout(client):
    client.post(reverse("accounts:logout"))


@pytest.mark.django_db
def test_remembered_device_skips_code_but_not_password(client, admin_a):
    response = login_with_code(client, admin_a, remember=True)
    cookie = response.cookies["guvenilen_cihaz"]
    assert cookie["httponly"] and int(cookie["max-age"]) == 30 * 24 * 3600
    assert TrustedDevice.objects.get(user=admin_a).token_hash not in cookie.value  # yalnızca özet saklanır
    logout(client)

    assert "Giriş yapıldı" not in post_login(client, admin_a.email, "yanlis-sifre").content.decode()
    assert "_auth_user_id" not in client.session  # şifre yine gerekli

    response = post_login(client, admin_a.email)
    assert response.url == DASH  # kod sorulmadan panel
    assert client.get(DASH).status_code == 200
    assert actions()[-1] == "auth.login"


@pytest.mark.django_db
def test_without_remember_code_is_asked_again(client, admin_a):
    response = login_with_code(client, admin_a, remember=False)
    assert "guvenilen_cihaz" not in response.cookies
    logout(client)
    assert post_login(client, admin_a.email).url == TWO_FA


@pytest.mark.django_db
def test_cookie_of_another_user_or_forged_value_is_ignored(client, admin_a, org_a):
    login_with_code(client, admin_a, remember=True)
    logout(client)
    other = make_user("diger@example.test")
    add_member(other, org_a)
    TOTPDevice.objects.create(user=other, name="t", confirmed=True)
    assert post_login(client, other.email).url == TWO_FA  # A'nın çerezi B'ye geçmez
    client.post(LOGIN)  # bekleyen doğrulamayı bırak
    device_id = client.cookies["guvenilen_cihaz"].value.split(":")[0]
    client.cookies["guvenilen_cihaz"] = f"{device_id}:uydurma"
    assert post_login(client, admin_a.email).url == TWO_FA


@pytest.mark.django_db
def test_expired_device_is_ignored(client, admin_a):
    login_with_code(client, admin_a, remember=True)
    logout(client)
    TrustedDevice.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    assert post_login(client, admin_a.email).url == TWO_FA


@pytest.mark.django_db
def test_forget_devices_from_security_page(client, admin_a):
    login_with_code(client, admin_a, remember=True)
    page = client.get(reverse("accounts:security")).content.decode()
    assert "Hatırlanan cihazlar" in page and "1 cihaz" in page
    client.post(reverse("accounts:security"), {"action": "forget_devices"})
    assert "auth.devices_revoked" in actions()
    logout(client)
    assert post_login(client, admin_a.email).url == TWO_FA


@pytest.mark.django_db
def test_password_reset_revokes_remembered_devices(client, admin_a):
    login_with_code(client, admin_a, remember=True)
    logout(client)
    admin_a.refresh_from_db()  # giriş last_login'i değiştirdi; bağlantı güncel bilgiyle üretilmeli
    uid = urlsafe_base64_encode(force_bytes(admin_a.pk))
    url = reverse("accounts:password_reset_confirm", args=[uid, default_token_generator.make_token(admin_a)])
    client.post(client.get(url).url, {"new_password1": "Yepyeni-Sifre-2026", "new_password2": "Yepyeni-Sifre-2026"})
    assert post_login(client, admin_a.email, "Yepyeni-Sifre-2026").url == TWO_FA


def test_device_label_is_readable():
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Safari/604.1"
    assert device_label(ua) == "Safari · iPhone"
    assert device_label("") == "Tarayıcı · bilinmeyen sistem"
