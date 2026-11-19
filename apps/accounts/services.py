from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

import qrcode
import qrcode.image.svg
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.audit.services import record
from apps.core.context import platform_context, tenant_context
from apps.tenancy.models import Membership

from .models import TrustedDevice

BACKUP_CODE_COUNT = 10


def primary_org_id(user) -> int | None:
    m = Membership.objects.filter(user=user, is_active=True).order_by("created_at").first()
    return m.organization_id if m else None


def audit_for_user(user, action: str, changes: dict | None = None) -> None:
    """Giriş olaylarını kullanıcının kuruluşuna yazar; kuruluşu yoksa platform kaydı olur."""
    org_id = primary_org_id(user) if user is not None else None
    if org_id is None:
        # Kuruluşsuz kayıt: yalnızca platform okuyabilir; yazma da platform bağlamında yapılır
        # (RETURNING için satırın görülebilir olması gerekir).
        with platform_context():
            record(action, changes=changes, actor=user)
        return
    with tenant_context(org_id):
        record(action, changes=changes, actor=user, organization_id=org_id)


def has_confirmed_totp(user) -> bool:
    return TOTPDevice.objects.filter(user=user, confirmed=True).exists()


def pending_totp_device(user) -> TOTPDevice:
    device = TOTPDevice.objects.filter(user=user, confirmed=False).first()
    return device or TOTPDevice.objects.create(user=user, name="Kimlik doğrulayıcı", confirmed=False)


def qr_svg(device: TOTPDevice) -> str:
    image = qrcode.make(device.config_url, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
    return image.to_string(encoding="unicode")


@transaction.atomic
def regenerate_backup_codes(user) -> list[str]:
    device, _ = StaticDevice.objects.get_or_create(user=user, name="Yedek kodlar")
    device.token_set.all().delete()
    codes = [StaticToken.random_token() for _ in range(BACKUP_CODE_COUNT)]
    for code in codes:
        device.token_set.create(token=code)
    return codes


# ---------------------------------------------------------------- güvenilen cihaz

TRUST_DAYS = 30
TRUST_COOKIE = "guvenilen_cihaz"


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


BROWSERS = (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari"))
SYSTEMS = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Mac OS X", "Mac"),
    ("Windows", "Windows"),
    ("Linux", "Linux"),
)


def device_label(user_agent: str) -> str:
    """Güvenlik sayfasında gösterilecek kaba cihaz adı (sıra önemli: Chrome da "Safari/" içerir)."""
    ua = user_agent or ""
    browser = next((name for key, name in BROWSERS if key in ua), "Tarayıcı")
    system = next((name for key, name in SYSTEMS if key in ua), "bilinmeyen sistem")
    return f"{browser} · {system}"


def trust_device(request, response, user) -> TrustedDevice:
    secret = secrets.token_urlsafe(32)
    device = TrustedDevice.objects.create(
        user=user,
        token_hash=_hash(secret),
        label=device_label(request.headers.get("User-Agent", "")),
        expires_at=timezone.now() + timedelta(days=TRUST_DAYS),
    )
    response.set_cookie(
        TRUST_COOKIE,
        f"{device.pk}:{secret}",
        max_age=TRUST_DAYS * 24 * 60 * 60,
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="Lax",
    )
    return device


def trusted_device_for(request, user) -> TrustedDevice | None:
    """Çerez bu kullanıcıya ait, süresi dolmamış ve iptal edilmemiş bir kayda karşılık geliyorsa döner."""
    raw = request.COOKIES.get(TRUST_COOKIE, "")
    device_id, _, secret = raw.partition(":")
    if not device_id.isdigit() or not secret:
        return None
    device = TrustedDevice.objects.filter(
        pk=int(device_id), user=user, revoked_at__isnull=True, expires_at__gt=timezone.now()
    ).first()
    if device is None or not hmac.compare_digest(device.token_hash, _hash(secret)):
        return None
    device.last_used_at = timezone.now()
    device.save(update_fields=["last_used_at"])
    return device


def active_trusted_devices(user):
    return TrustedDevice.objects.filter(user=user, revoked_at__isnull=True, expires_at__gt=timezone.now()).order_by(
        "-created_at"
    )


def revoke_trusted_devices(user) -> int:
    return active_trusted_devices(user).update(revoked_at=timezone.now())
