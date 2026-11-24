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
    "block.created": "Blok eklendi",
    "block.updated": "Blok güncellendi",
    "unit_type.created": "Bölüm tipi eklendi",
    "unit_type.updated": "Bölüm tipi güncellendi",
    "unit.created": "Bölüm eklendi",
    "unit.bulk_created": "Bölümler toplu eklendi",
    "unit.updated": "Bölüm güncellendi",
    "unit.archived": "Bölüm arşivlendi",
    "unit.restored": "Bölüm arşivden çıkarıldı",
    "person.created": "Kişi eklendi",
    "person.updated": "Kişi güncellendi",
    "person.anonymized": "Kişi anonimleştirildi",
    "occupancy.created": "Bölüme kişi bağlandı",
    "occupancy.ended": "Bölüm ilişkisi sonlandırıldı",
    "ledger.setup": "Hesap planı kuruldu",
    "account.created": "Hesap eklendi",
    "account.updated": "Hesap güncellendi",
    "period.locked": "Dönem kilitlendi",
    "agreement.created": "Özel aidat anlaşması eklendi",
    "agreement.ended": "Özel aidat anlaşması sonlandırıldı",
    "charge_type.created": "Borç türü eklendi",
    "charge_type.updated": "Borç türü güncellendi",
    "batch.drafted": "Tahakkuk taslağı hazırlandı",
    "batch.discarded": "Tahakkuk taslağı silindi",
    "batch.approved": "Tahakkuk onaylandı",
    "batch.cancelled": "Tahakkuk iptal edildi",
    "payment.created": "Tahsilat alındı",
    "payment.cancelled": "Tahsilat iptal edildi",
    "payment.reallocated": "Tahsilat eşleşmesi düzeltildi",
    "advance.applied": "Avans borca mahsup edildi",
    "vendor.created": "Tedarikçi eklendi",
    "expense.created": "Gider kaydedildi",
    "expense.cancelled": "Gider iptal edildi",
    "transfer.created": "Virman yapıldı",
    "opening.debts": "Açılış borçları girildi",
    "opening.advances": "Açılış avansları girildi",
    "import.uploaded": "Excel dosyası yüklendi",
    "import.applied": "Excel içe aktarması uygulandı",
    "import.discarded": "Excel içe aktarmasından vazgeçildi",
    "resident.login": "Sakin giriş yaptı",
    "resident.consent": "Sakin aydınlatma metnini okudu",
    "resident.password_set": "Sakin şifre belirledi",
    "portal.updated": "Sakin portalı ayarları değişti",
    "notification.sent": "Bildirim gönderildi",
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
