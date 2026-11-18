"""Giriş akışı.

1. E-posta + şifre (kilitleme: e-posta başına 5, IP başına 30 hatalı deneme / 15 dk).
2. Kullanıcının onaylı doğrulayıcısı varsa: henüz oturum açılmaz, kod sorulur.
   Kod (ya da tek kullanımlık yedek kod) doğruysa oturum açılır.
3. Doğrulayıcısı yoksa: oturum açılır ama panel kilitli kalır; önce kurulum yapılır.

Hata mesajları geneldir: e-postanın sistemde olup olmadığı dışarıdan anlaşılmaz.
"""

import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth import views as auth_views
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.debug import sensitive_post_parameters
from django_otp import login as otp_login
from django_otp import match_token
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.core.middleware import client_ip
from apps.tenancy.permissions import panel_view

from . import services, throttle
from .forms import LoginForm, SetPasswordForm, TokenForm

GENERIC_ERROR = "E-posta ya da şifre hatalı."
LOCKED_ERROR = "Çok fazla hatalı deneme. Lütfen 15 dakika sonra tekrar deneyin."
PENDING_KEY, PENDING_AT, NEXT_KEY, OTP_TRIES = "pending_2fa_user", "pending_2fa_at", "login_next", "otp_tries"
PENDING_SECONDS = 5 * 60
MAX_OTP_TRIES = 5


def _safe_next(request, value: str | None) -> str:
    if value and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return value
    return reverse_lazy(settings.LOGIN_REDIRECT_URL)


@sensitive_post_parameters("password")
def login_view(request):
    if request.user.is_authenticated and request.user.is_verified():
        return redirect(settings.LOGIN_REDIRECT_URL)
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        ip = client_ip(request)
        if throttle.is_locked(email, ip):
            services.audit_for_user(None, "auth.login_locked")
            form.add_error(None, LOCKED_ERROR)
        else:
            user = authenticate(request, username=email, password=form.cleaned_data["password"])
            if user is None:
                throttle.register_failure(email, ip)
                known = get_user_model().objects.filter(email=email).first()
                services.audit_for_user(known, "auth.login_failed")
                form.add_error(None, GENERIC_ERROR)
            else:
                throttle.clear(email)
                request.session[NEXT_KEY] = str(_safe_next(request, request.GET.get("next")))
                if services.has_confirmed_totp(user):
                    request.session[PENDING_KEY] = user.pk
                    request.session[PENDING_AT] = time.time()
                    request.session[OTP_TRIES] = 0
                    return redirect("accounts:two_factor")
                login(request, user)
                services.audit_for_user(user, "auth.login", {"2fa": "kurulum bekliyor"})
                return redirect("accounts:two_factor" if settings.REQUIRE_2FA else request.session.pop(NEXT_KEY))
    return render(request, "accounts/login.html", {"form": form})


def _throttle_message(user) -> str | None:
    """django-otp hatalı denemeden sonra kısa bir bekleme uygular; kullanıcıya bunu söyle."""
    for device in TOTPDevice.objects.filter(user=user):
        allowed, info = device.verify_is_allowed()
        if not allowed and info and info.get("locked_until"):
            seconds = max(1, int((info["locked_until"] - timezone.now()).total_seconds()) + 1)
            return f"Hatalı denemeden sonra kısa bir bekleme uygulanıyor. {seconds} saniye sonra tekrar deneyin."
    return None


def two_factor_view(request):
    """Kod doğrulama (doğrulayıcısı olan) ya da ilk kurulum (olmayan)."""
    User = get_user_model()
    pending_id = request.session.get(PENDING_KEY)

    # --- Kurulum: oturum açık, doğrulayıcı yok
    if request.user.is_authenticated and not services.has_confirmed_totp(request.user):
        device = services.pending_totp_device(request.user)
        form = TokenForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            if wait := _throttle_message(request.user):
                form.add_error("token", wait)
            elif device.verify_token(form.cleaned_data["token"]):
                device.confirmed = True
                device.save(update_fields=["confirmed"])
                otp_login(request, device)
                request.session["new_backup_codes"] = services.regenerate_backup_codes(request.user)
                services.audit_for_user(request.user, "auth.2fa_enabled")
                return redirect("accounts:backup_codes")
            else:
                form.add_error("token", "Kod doğrulanamadı. Telefonunuzun saatinin doğru olduğundan emin olun.")
        return render(
            request,
            "accounts/two_factor_setup.html",
            {"form": form, "qr": services.qr_svg(device), "secret": device.key},
        )

    if request.user.is_authenticated and request.user.is_verified():
        return redirect(settings.LOGIN_REDIRECT_URL)

    # --- Doğrulama: şifre doğru, kod bekleniyor
    if not pending_id or time.time() - request.session.get(PENDING_AT, 0) > PENDING_SECONDS:
        for key in (PENDING_KEY, PENDING_AT, OTP_TRIES):
            request.session.pop(key, None)
        messages.info(request, "Oturum süresi doldu, lütfen tekrar giriş yapın.")
        return redirect("accounts:login")
    user = User.objects.get(pk=pending_id)
    form = TokenForm(request.POST or None)
    if request.method == "POST" and form.is_valid() and (wait := _throttle_message(user)):
        form.add_error("token", wait)
    elif request.method == "POST" and form.is_valid():
        device = match_token(user, form.cleaned_data["token"])
        if device is not None:
            next_url = request.session.pop(NEXT_KEY, None)
            for key in (PENDING_KEY, PENDING_AT, OTP_TRIES):
                request.session.pop(key, None)
            login(request, user)
            otp_login(request, device)
            used_backup = isinstance(device, StaticDevice)
            services.audit_for_user(user, "auth.login", {"2fa": "yedek kod" if used_backup else "uygulama"})
            if used_backup:
                messages.warning(
                    request,
                    "Yedek kod kullandınız; her kod bir kez çalışır. "
                    "Kalan kodlarınızı Güvenlik sayfasından yenileyebilirsiniz.",
                )
            return redirect(next_url or settings.LOGIN_REDIRECT_URL)
        request.session[OTP_TRIES] = request.session.get(OTP_TRIES, 0) + 1
        services.audit_for_user(user, "auth.2fa_failed")
        if request.session[OTP_TRIES] >= MAX_OTP_TRIES:
            for key in (PENDING_KEY, PENDING_AT, OTP_TRIES):
                request.session.pop(key, None)
            throttle.register_failure(user.email, client_ip(request))
            messages.error(request, "Çok fazla hatalı kod. Lütfen tekrar giriş yapın.")
            return redirect("accounts:login")
        form.add_error("token", "Kod hatalı.")
    return render(request, "accounts/two_factor_verify.html", {"form": form})


def backup_codes_view(request):
    codes = request.session.pop("new_backup_codes", None)
    if not request.user.is_authenticated or not request.user.is_verified() or not codes:
        return redirect(settings.LOGIN_REDIRECT_URL)
    return render(request, "accounts/backup_codes.html", {"codes": codes})


def logout_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if request.user.is_authenticated:
        services.audit_for_user(request.user, "auth.logout")
    logout(request)
    messages.info(request, "Çıkış yaptınız.")
    return redirect("accounts:login")


@panel_view()
def security_view(request):
    if request.method == "POST":
        request.session["new_backup_codes"] = services.regenerate_backup_codes(request.user)
        services.audit_for_user(request.user, "auth.backup_codes_regenerated")
        return redirect("accounts:backup_codes")
    remaining = StaticDevice.objects.filter(user=request.user).values_list("token_set__id", flat=True)
    return render(request, "accounts/security.html", {"backup_remaining": len([r for r in remaining if r])})


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "registration/password_reset_confirm.html"
    form_class = SetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        services.audit_for_user(form.user, "auth.password_changed")
        return response
