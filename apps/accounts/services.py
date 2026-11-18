from __future__ import annotations

import qrcode
import qrcode.image.svg
from django.db import transaction
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.audit.services import record
from apps.core.context import platform_context, tenant_context
from apps.tenancy.models import Membership

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
