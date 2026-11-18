from django.conf import settings
from django.db import models

from apps.tenancy.models import Organization


class AuditEvent(models.Model):
    """Değiştirilemez işlem kaydı. Veritabanı tetikleyicisi UPDATE ve DELETE'i reddeder.

    `organization` boş olabilir (ör. bilinmeyen e-postayla başarısız giriş); bu kayıtları
    yalnızca platform görür. Kişisel veri (isim, telefon) `changes` içine düz yazılmaz.
    """

    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    site_id = models.BigIntegerField(null=True, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    actor_label = models.CharField(max_length=200, blank=True)
    action = models.CharField(max_length=60, db_index=True)
    target_type = models.CharField(max_length=60, blank=True)
    target_id = models.CharField(max_length=60, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "denetim kaydı"
        verbose_name_plural = "denetim kayıtları"
        ordering = ["-id"]
        indexes = [models.Index(fields=["organization", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.created_at:%d.%m.%Y %H:%M} {self.action}"
