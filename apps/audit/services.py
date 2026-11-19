from __future__ import annotations

from django.db import models

from apps.core import context

from .models import AuditEvent

ACTIONS: dict[str, str] = {
    "auth.login": "Giriş yapıldı",
    "auth.login_failed": "Hatalı giriş",
    "auth.login_locked": "Giriş kilitlendi",
    "auth.logout": "Çıkış yapıldı",
    "auth.2fa_enabled": "İki adımlı doğrulama açıldı",
    "auth.2fa_failed": "Hatalı doğrulama kodu",
    "auth.backup_codes_regenerated": "Yedek kodlar yenilendi",
    "auth.password_changed": "Şifre değişti",
    "auth.device_trusted": "Cihaz hatırlandı",
    "auth.devices_revoked": "Hatırlanan cihazlar silindi",
    "org.created": "Kuruluş oluşturuldu",
    "org.switched": "Kuruluş değiştirildi",
    "member.added": "Üye eklendi",
    "site.created": "Site oluşturuldu",
    "site.updated": "Site güncellendi",
}


def record(
    action: str,
    target: models.Model | None = None,
    changes: dict | None = None,
    *,
    organization_id: int | None = None,
    actor=None,
    site_id: int | None = None,
) -> AuditEvent:
    """İşlemi denetim izine yazar. Çağıran işlemle aynı transaction içinde çalışır."""
    if action not in ACTIONS:
        raise ValueError(f"Tanımsız denetim işlemi: {action}")
    actor_id = actor.pk if actor is not None else context.current_actor_id()
    label = ""
    if actor is not None:
        label = getattr(actor, "email", "") or str(actor)
    return AuditEvent.objects.create(
        organization_id=organization_id if organization_id is not None else context.current_org_id(),
        site_id=site_id,
        actor_id=actor_id,
        actor_label=label,
        action=action,
        target_type=target._meta.label_lower if target is not None else "",
        target_id=str(target.pk) if target is not None else "",
        changes=changes or {},
        ip=context.current_ip(),
    )
